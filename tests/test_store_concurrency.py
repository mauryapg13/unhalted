"""Concurrent access to the store.

FastAPI runs sync handlers in a threadpool, so two webhooks arriving together
reach the store at the same time on one sqlite3 connection. Before the lock, one
request's commit could land another's half-written rows and its rollback could
discard writes that had succeeded — silently, because `check_same_thread=False`
suppresses the error sqlite3 would otherwise raise.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from unhalted.agent import handle_failure
from unhalted.models import FailureSignal
from unhalted.shell.windows import IST
from unhalted.store import Store

WORKERS = 12


def signal(n: int) -> FailureSignal:
    return FailureSignal(
        payment_id=f"pay_CONC{n:04d}",
        customer_ref=f"cust_{n:04d}",
        amount_paise=49900,
        occurred_at=datetime(2026, 9, 1, 14, 0, tzinfo=IST),
        source="test",
        error_reason="insufficient_funds",
        error_source="customer",
        error_step="payment_authorization",
    )


def test_concurrent_failures_each_get_their_own_intact_case(tmp_path) -> None:
    store = Store(str(tmp_path / "c.db"))
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            cases = list(pool.map(lambda n: handle_failure(store, signal(n)), range(WORKERS)))

        assert len({c.id for c in cases}) == WORKERS, "cases collided"
        assert len(store.all_cases()) == WORKERS, "a case was lost"

        for case in cases:
            # Each case must carry its own complete history, not a fragment of
            # someone else's transaction.
            assert len(store.signals(case.id)) == 1
            assert store.latest_diagnosis(case.id) is not None
            kinds = [r.decision_type for r in store.timeline(case.id)]
            assert kinds == ["ingest", "diagnosis", "escalation", "schedule"], kinds
    finally:
        store.close()


def test_the_same_payment_arriving_concurrently_opens_exactly_one_case(tmp_path) -> None:
    """Razorpay redelivers, and redeliveries can overlap."""
    store = Store(str(tmp_path / "d.db"))
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            cases = list(pool.map(lambda _: handle_failure(store, signal(1)), range(WORKERS)))

        assert len({c.id for c in cases}) == 1
        assert len(store.all_cases()) == 1
    finally:
        store.close()


# -- durability ---------------------------------------------------------------


def test_the_store_uses_write_ahead_logging(tmp_path) -> None:
    """A reader must not block the writer, and a crash mid-write must leave the
    database recoverable from the log rather than from a rollback journal."""
    store = Store(str(tmp_path / "wal.db"))
    try:
        with store._read() as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        store.close()


def test_every_commit_reaches_the_disk_before_returning(tmp_path) -> None:
    """synchronous=FULL. A case the agent believes it recorded has to survive
    the power going out — Razorpay does not redeliver forever, and a lost case
    is money nobody chases."""
    store = Store(str(tmp_path / "sync.db"))
    try:
        with store._read() as conn:
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        store.close()


def test_a_case_survives_the_process_that_wrote_it(tmp_path) -> None:
    """The closest thing to a crash test we can run in-process: write, close
    without ceremony, reopen, and expect everything to be there."""
    path = str(tmp_path / "durable.db")
    store = Store(path)
    case = handle_failure(store, signal(99))
    store.schedule_action(case.id, case.customer_ref, "nudge", None, case.opened_at)
    store.close()

    reopened = Store(path)
    try:
        recovered = reopened.get_case(case.id)
        assert recovered is not None
        assert recovered.id == case.id
        assert len(reopened.signals(case.id)) == 1
        assert len(reopened.timeline(case.id)) == 4
        # what handle_failure scheduled — a balance failure asks when to
        # try and arms a fallback retry behind it — plus the nudge above
        assert len(reopened.pending_actions(case_id=case.id)) == 3
    finally:
        reopened.close()


# --- leasing across processes (#31) -----------------------------------------
#
# Threads share this module's Store and its lock, so they cannot show what two
# *deployed* workers would do. These use real processes against one file.


def _claim_in_a_separate_process(path, name, due_iso, queue):  # pragma: no cover
    """Runs in a child process, so it must be importable at module level."""
    from datetime import datetime, timedelta

    from unhalted.store import Store

    due = datetime.fromisoformat(due_iso)
    store = Store(path)
    claimed = []
    try:
        for _ in range(20):
            rows = store.lease_due_actions(
                due, worker=name, lease_for=timedelta(minutes=5), limit=7
            )
            if not rows:
                break
            claimed.extend(int(r["id"]) for r in rows)
        queue.put((name, claimed, None))
    except Exception as exc:  # noqa: BLE001 - the child reports, the parent asserts
        queue.put((name, [], f"{type(exc).__name__}: {exc}"))
    finally:
        store.close()


def test_four_processes_never_claim_the_same_action(tmp_path) -> None:
    """The regression that matters most in this file.

    An earlier lease read its claim back with `WHERE worker = ? AND
    leased_until = ?`, which is not a unique key — the same worker claiming
    twice inside one window re-read its earlier batch. Four processes over 400
    actions produced 386 double-claims, and every one would have been a debit
    attempted twice. `UPDATE ... RETURNING` makes claim and read one statement.
    """
    import multiprocessing as mp
    from datetime import timedelta

    path = str(tmp_path / "mw.db")
    now = datetime(2026, 9, 3, 10, 0, tzinfo=IST)
    due = now + timedelta(days=2)

    store = Store(path)
    for i in range(60):
        case = handle_failure(store, signal(900 + i), now=now)
        store.schedule_action(case.id, case.customer_ref, "nudge", now, now)
    # Only what is actually due by then: a balance failure also arms a
    # fallback retry a day and a half out, which no worker should claim
    # at `due` and which would otherwise read here as work that went missing.
    expected = sum(
        1 for a in store.pending_actions()
        if a["scheduled_for"] and datetime.fromisoformat(a["scheduled_for"]) <= due
    )
    store.close()

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    workers = [
        ctx.Process(target=_claim_in_a_separate_process,
                    args=(path, f"w{i}", due.isoformat(), queue))
        for i in range(4)
    ]
    for w in workers:
        w.start()
    results = [queue.get(timeout=60) for _ in workers]
    for w in workers:
        w.join(timeout=60)

    errors = [e for _, _, e in results if e]
    assert not errors, f"a worker failed: {errors}"

    all_claims = [i for _, claims, _ in results for i in claims]
    assert len(all_claims) == len(set(all_claims)), "an action was claimed twice"
    assert len(set(all_claims)) == expected, "some actions were never claimed"


def test_a_redelivered_payment_is_reported_as_known_not_as_opened(tmp_path) -> None:
    """The audit trail must not claim an opening that did not happen.

    Razorpay redelivers, and re-running the demo script sends the same payment
    again. Both correctly match the existing case; recording "case-opened" for
    either made the trail assert an event that never occurred.
    """
    now = datetime(2026, 9, 3, 11, 0, tzinfo=IST)
    store = Store(str(tmp_path / "dupe.db"))
    try:
        first = handle_failure(store, signal(1), now=now)
        second = handle_failure(store, signal(1), now=now)
        assert first.id == second.id, "one payment is one case"

        ingests = [r.action for r in store.timeline(first.id)
                   if r.decision_type == "ingest"]
        assert ingests == ["case-opened", "signal already known; case is open"]
    finally:
        store.close()


def test_novelty_is_decided_under_the_same_lock_as_the_case(tmp_path) -> None:
    store = Store(str(tmp_path / "novel.db"))
    try:
        _, created_first = store.open_case_or_get(signal(2))
        _, created_again = store.open_case_or_get(signal(2))
        assert created_first is True
        assert created_again is False
    finally:
        store.close()


def test_actions_can_be_read_in_any_state(tmp_path) -> None:
    """The scheduler view needs cancelled rows too — an action stopped by a
    rule is an event worth showing, and `pending_actions` cannot return it."""
    now = datetime(2026, 9, 3, 11, 0, tzinfo=IST)
    store = Store(str(tmp_path / "states.db"))
    try:
        case = handle_failure(store, signal(7), now=now)
        assert len(store.actions(state="pending")) >= 1
        assert store.actions(state="cancelled") == []

        store.cancel_pending("REVOKED", case_id=case.id)
        assert store.actions(state="pending") == []
        assert len(store.actions(state="cancelled")) >= 1
        assert len(store.actions()) >= 1, "no state filter means every state"
    finally:
        store.close()
