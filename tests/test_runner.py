"""The runner: what makes a scheduled action actually happen.

The interesting assertions here are not "it ran the thing". They are the ones
about two workers, a dead worker, and a customer who revokes while a worker is
holding the lease — because those are the cases where a queue built on a table
either is durable or quietly is not.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from unhalted.agent import apply_stop, handle_failure
from unhalted.models import CaseState, FailureSignal
from unhalted.runner import LEASE, Outcome, execute_nudge, run_due
from unhalted.shell import paylink
from unhalted.shell.windows import IST
from unhalted.store import Store

NOW = datetime(2026, 9, 3, 10, 0, tzinfo=IST)
LATER = NOW + timedelta(days=2)


def signal(**over):
    # A technical failure, deliberately: these tests are about the runner's
    # own mechanics — leasing, claiming, reclaiming, cancellation — and need
    # a case that schedules a *retry*. A balance failure now enters the
    # ladder at NUDGE (it asks the customer when to try, rather than guessing
    # three times), which would give these tests a nudge to reason about
    # instead of the retry every one of them is written around.
    fields = {
        "payment_id": "pay_RUN", "customer_ref": "cust_run", "amount_paise": 49900,
        "occurred_at": NOW, "source": "test", "method": "card",
        "error_reason": "gateway_technical_error", "error_source": "gateway",
    }
    fields.update(over)
    return FailureSignal(**fields)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "run.db"))
    yield s
    s.close()


@pytest.fixture
def case(store):
    return handle_failure(store, signal(), now=NOW)


def ok(_store, _action, _now):
    return Outcome("done", "did the thing")


def explodes(_store, _action, _now):
    raise RuntimeError("the adapter fell over")


# -- due-ness ----------------------------------------------------------------


def test_an_action_that_is_not_due_is_left_alone(store, case) -> None:
    report = run_due(store, now=NOW, executors={"retry": ok})
    assert report.claimed == 0
    assert len(store.pending_actions()) == 1


def test_an_action_that_is_due_is_executed(store, case) -> None:
    report = run_due(store, now=LATER, executors={"retry": ok})
    assert report.claimed == 1
    assert report.done == 1
    assert store.pending_actions() == []


def test_running_twice_does_not_execute_twice(store, case) -> None:
    """Idempotent by construction: the second pass finds nothing due. This is
    what lets a scheduler retry a timed-out call without double-charging."""
    first = run_due(store, now=LATER, executors={"retry": ok})
    second = run_due(store, now=LATER, executors={"retry": ok})
    assert first.done == 1
    assert second.claimed == 0


# -- two workers -------------------------------------------------------------


def test_two_workers_cannot_claim_the_same_action(store, case) -> None:
    """Claim and read are one transaction. Selecting first and updating after
    is how the same retry reaches two workers."""
    a = store.lease_due_actions(LATER, worker="a", lease_for=LEASE)
    b = store.lease_due_actions(LATER, worker="b", lease_for=LEASE)
    assert len(a) == 1
    assert b == [], "the second worker took work the first was already holding"


def test_a_leased_action_is_not_pending(store, case) -> None:
    store.lease_due_actions(LATER, worker="a", lease_for=LEASE)
    assert store.pending_actions() == []


# -- a worker that dies ------------------------------------------------------


def test_a_lease_that_expires_returns_the_work(store, case) -> None:
    """The crash case. A worker that never comes back must not strand a retry."""
    store.lease_due_actions(LATER, worker="dead", lease_for=LEASE)
    assert store.pending_actions() == []

    reclaimed = store.release_expired_leases(LATER + LEASE + timedelta(seconds=1))
    assert reclaimed == 1
    assert len(store.pending_actions()) == 1


def test_the_runner_reclaims_before_it_claims(store, case) -> None:
    store.lease_due_actions(LATER, worker="dead", lease_for=LEASE)
    report = run_due(
        store, now=LATER + LEASE + timedelta(minutes=1), executors={"retry": ok}
    )
    assert report.reclaimed == 1
    assert report.done == 1


def test_a_live_lease_is_not_stolen(store, case) -> None:
    store.lease_due_actions(LATER, worker="busy", lease_for=LEASE)
    assert store.release_expired_leases(LATER + timedelta(minutes=1)) == 0


# -- cancellation wins -------------------------------------------------------


def test_a_revocation_cancels_an_action_a_worker_is_holding(store, case) -> None:
    """The race that matters. A customer revoking mid-flight must still stop
    the charge, so cancellation reaches leased rows and the runner re-reads."""
    store.lease_due_actions(LATER, worker="holder", lease_for=LEASE)
    cancelled = store.cancel_pending("REVOKED", case_id=case.id)
    assert cancelled == 1, "a leased action must still be cancellable"


def test_the_runner_refuses_to_execute_a_cancelled_action(store, case) -> None:
    def cancel_then_run(_store, action, _now):  # pragma: no cover - not reached
        raise AssertionError("a cancelled action must not execute")

    store.cancel_pending("REVOKED", case_id=case.id)
    report = run_due(store, now=LATER, executors={"retry": cancel_then_run})
    assert report.claimed == 0


def test_a_stop_rule_leaves_nothing_for_the_runner_to_do(store, case) -> None:
    apply_stop(
        store, "REVOKED", case_id=case.id, customer_ref=case.customer_ref,
        detail="customer revoked the mandate", now=NOW,
    )
    report = run_due(store, now=LATER, executors={"retry": ok})
    assert report.claimed == 0
    assert report.done == 0


# -- failure is contained ----------------------------------------------------


def test_one_exploding_action_does_not_stop_the_others(store) -> None:
    """A bad row must not stall every other customer's recovery."""
    a = handle_failure(store, signal(payment_id="pay_A", customer_ref="cust_a"), now=NOW)
    handle_failure(store, signal(payment_id="pay_B", customer_ref="cust_b"), now=NOW)

    def selective(_store, action, _now):
        if action["case_id"] == a.id:
            raise RuntimeError("boom")
        return Outcome("done", "fine")

    report = run_due(store, now=LATER, executors={"retry": selective})
    assert report.claimed == 2
    assert report.failed == 1
    assert report.done == 1


def test_a_failure_is_recorded_against_the_action(store, case) -> None:
    run_due(store, now=LATER, executors={"retry": explodes})
    action = store.action(1)
    assert action["state"] == "failed"
    assert "RuntimeError" in action["last_error"]


# -- what the executors do ---------------------------------------------------


def test_a_debit_is_refused_because_this_deployment_cannot_make_one(store, case) -> None:
    """Absent, not stubbed. The decision stands; the execution does not exist."""
    report = run_due(store, now=LATER)
    assert report.held == 1
    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN
    assert any("cannot initiate a debit" in line for line in report.lines)


def test_an_action_with_no_executor_is_not_silently_dropped(store, case) -> None:
    report = run_due(store, now=LATER, executors={})
    assert report.no_adapter == 1
    assert store.action(1)["state"] == "no-adapter"


def test_a_nudge_outside_contact_hours_is_deferred_not_failed(store, case) -> None:
    """Becoming due at 02:00 is not a reason to message somebody at 02:00."""
    store.schedule_action(case.id, case.customer_ref, "nudge", NOW, NOW)
    night = datetime(2026, 9, 5, 2, 0, tzinfo=IST)
    report = run_due(store, now=night)

    nudge = next(a for a in store.pending_actions() if a["kind"] == "nudge")
    assert nudge["state"] == "pending", "it should wait, not fail"
    assert any("not sent" in line for line in report.lines)


def test_a_deferred_nudge_never_reaches_the_payment_link_call(store, case) -> None:
    """A link is a real network call; one deferred for contact hours must not
    spend it on a message that is not going out this pass."""
    store.schedule_action(case.id, case.customer_ref, "nudge", NOW, NOW)
    night = datetime(2026, 9, 5, 2, 0, tzinfo=IST)
    with patch.object(paylink, "create_payment_link") as mocked:
        run_due(store, now=night)
    mocked.assert_not_called()


def test_a_sent_nudge_carries_the_pay_link_when_one_is_generated(store, case) -> None:
    action_id = store.schedule_action(case.id, case.customer_ref, "nudge", NOW, NOW)
    action = store.action(action_id)
    with patch.object(
        paylink, "create_payment_link",
        return_value=paylink.PaymentLink(url="https://rzp.io/i/AbC123", id="plink_X",
                                         status="created"),
    ) as mocked:
        outcome = execute_nudge(store, action, NOW)

    mocked.assert_called_once()
    assert outcome.state == "done"


def test_a_nudge_still_sends_when_the_link_cannot_be_generated(store, case) -> None:
    """No adapter for the link is not a reason to withhold the message itself."""
    action_id = store.schedule_action(case.id, case.customer_ref, "nudge", NOW, NOW)
    action = store.action(action_id)
    with patch.object(paylink, "create_payment_link", return_value=None):
        outcome = execute_nudge(store, action, NOW)

    assert outcome.state == "done"


# -- the audit trail ---------------------------------------------------------


def test_every_execution_is_recorded_beside_its_decision(store, case) -> None:
    """A decision recorded without its execution is half an account."""
    run_due(store, now=LATER, worker="worker-7", executors={"retry": ok})
    executions = [r for r in store.timeline(case.id) if r.decision_type == "execution"]
    assert len(executions) == 1
    assert executions[0].inputs["worker"] == "worker-7"
    assert executions[0].inputs["attempt"] == 1
    assert executions[0].rules_fired == ["RUNNER"]
