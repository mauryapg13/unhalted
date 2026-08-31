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
            assert kinds == ["ingest", "diagnosis", "schedule"], kinds
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
