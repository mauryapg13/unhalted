"""A stop outlives the queue it emptied.

The bug these exist for: `OPT_OUT` cancelled every pending action and then
left no trace. The case stayed `open`, nothing recorded that the customer had
asked to be left alone, and the next message they sent re-armed the ladder —
a retry scheduled on somebody who had said STOP two minutes earlier. The audit
line even said "continuing is a compliance failure, not a lost sale" while the
system went on to do it.

Cancelling what is queued is the easy half. These tests are the other half.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from unhalted.agent import apply_stop, handle_failure, handle_reply
from unhalted.models import CaseState, FailureSignal
from unhalted.runner import run_due
from unhalted.shell.windows import IST
from unhalted.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "durable.db"))
    yield s
    s.close()


NOW = datetime(2026, 9, 3, 11, 0, tzinfo=IST)
LATER = datetime(2026, 9, 3, 11, 5, tzinfo=IST)


def signal(payment_id: str = "pay_DUR0001", customer: str = "cust_durable") -> FailureSignal:
    return FailureSignal(
        payment_id=payment_id,
        customer_ref=customer,
        amount_paise=49900,
        method="card",
        error_reason="insufficient_fund",
        error_source="customer",
        occurred_at=NOW,
        source="test",
    )


def opted_out(store: Store) -> tuple[str, str]:
    case = handle_failure(store, signal(), now=NOW)
    apply_stop(
        store, "OPT_OUT", case_id=case.id, customer_ref=case.customer_ref,
        detail="STOP", now=NOW,
    )
    return case.id, case.customer_ref


def test_an_opt_out_is_recorded_beyond_the_actions_it_cancelled(store: Store) -> None:
    case_id, customer_ref = opted_out(store)

    standing = store.contact_suppression(case_id=case_id, customer_ref=customer_ref)
    assert standing is not None, "the stop has to survive the instant it fired"
    assert standing["code"] == "OPT_OUT"
    assert standing["scope"] == "customer", (
        "an opt-out is given by a person, not by a case"
    )


def test_a_later_reply_cannot_lift_an_opt_out(store: Store) -> None:
    """The exact sequence that was wrong: STOP, then "actually, continue"."""
    case_id, _ = opted_out(store)
    case = store.get_case(case_id)

    _, outcome = handle_reply(store, case, "actually, i want to continue", now=LATER)

    assert outcome.rules_fired == ["CONTACT_SUPPRESSED:OPT_OUT"]
    assert outcome.needs_human, "a person is the only route back"
    assert store.pending_actions(case_id=case_id) == [], (
        "no reply re-arms the ladder for somebody who asked not to be contacted"
    )


def test_a_date_named_after_a_stop_schedules_nothing(store: Store) -> None:
    """A date is not consent.

    This is the one that actually fired in the demo: "next tuesday" parsed as a
    promise-to-pay at 0.85 and scheduled a retry, because the promise path
    never asked whether this customer was still contactable.
    """
    case_id, _ = opted_out(store)
    case = store.get_case(case_id)

    handle_reply(store, case, "next tuesday", now=LATER)

    assert store.pending_actions(case_id=case_id) == []


def test_a_new_failure_for_a_stopped_customer_schedules_nothing(store: Store) -> None:
    """The reach a customer-scoped stop is supposed to have.

    Their next renewal fails a month later. The money is owed and the case
    opens, but nothing may be sent about it.
    """
    _, customer_ref = opted_out(store)

    later = handle_failure(
        store, signal("pay_DUR0002", customer_ref),
        now=datetime(2026, 10, 3, 11, 0, tzinfo=IST),
    )

    assert store.pending_actions(case_id=later.id) == []
    assert later.state is CaseState.HELD_FOR_HUMAN
    assert store.get_case(later.id) is not None, (
        "the failure is still recorded — suppressed contact is not a hidden case"
    )


def test_the_runner_refuses_an_action_armed_before_the_stop(store: Store) -> None:
    """Defence in depth, because delivery is at-least-once.

    An action leased by a worker that then died returns to the pool. If the
    stop landed in between, the row is still there to be claimed again — so
    the execution side checks too, not only the scheduling side.
    """
    case = handle_failure(store, signal("pay_DUR0003", "cust_armed"), now=NOW)
    assert store.pending_actions(case_id=case.id), "setup: something is queued"

    # Suppress the customer without going through cancel_pending, which is what
    # a stop on one of their *other* cases would leave behind here.
    store.suppress_contact(
        scope="customer", scope_key="cust_armed", code="OPT_OUT", at=NOW,
    )

    report = run_due(store, now=LATER)

    assert report.done == 0, "nothing was delivered"
    assert report.cancelled >= 1
    assert any("contact suppressed" in line for line in report.lines)


def test_only_a_named_person_lifts_a_suppression(store: Store) -> None:
    case_id, customer_ref = opted_out(store)
    standing = store.contact_suppression(case_id=case_id, customer_ref=customer_ref)

    store.lift_suppression(int(standing["id"]), by="ops@acme.test", at=LATER)

    assert store.contact_suppression(
        case_id=case_id, customer_ref=customer_ref
    ) is None, "a lifted suppression no longer bars contact"
