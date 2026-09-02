"""Verifying that a failure was really a failure.

Razorpay documents the case this exists for and calls it expected: a
`payment.failed` webhook can be followed by `payment.captured` for the same
transaction, because the customer retried inside their own UPI app and
succeeded. Retrying such a case debits somebody who has already paid, which is a
worse outcome than never recovering at all.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from unhalted.agent import handle_failure
from unhalted.core.diagnose import needs_verification
from unhalted.models import CaseState, FailureSignal
from unhalted.shell.verify import Verification, VerificationUnavailable
from unhalted.shell.windows import IST
from unhalted.store import Store

NOW = datetime(2026, 9, 20, 14, 0, tzinfo=IST)


def signal(
    reason: str = "payment_timed_out",
    order: str | None = "order_ABC",
    source: str = "gateway",
) -> FailureSignal:
    return FailureSignal(
        payment_id=f"pay_{reason}_{order}",
        order_id=order,
        customer_ref="cust_verify",
        amount_paise=49900,
        occurred_at=NOW,
        source="test",
        method="upi",
        error_reason=reason,
        error_source=source,
    )


class Settled:
    def order_settled(self, order_id: str) -> Verification:
        return Verification(True, f"order {order_id} was paid by pay_LATER", "2 payments")


class Unpaid:
    def order_settled(self, order_id: str) -> Verification:
        return Verification(False, f"order {order_id} has no captured payment", "1 payment")


class Broken:
    def order_settled(self, order_id: str) -> Verification:
        raise VerificationUnavailable("Razorpay unreachable")


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "v.db"))
    yield s
    s.close()


# -- when to ask --------------------------------------------------------------


@pytest.mark.parametrize(
    "reason", ["payment_timed_out", "gateway_technical_error", "payment_collect_request_expired"]
)
def test_an_unsettled_outcome_is_verified_first(reason: str) -> None:
    assert needs_verification(signal(reason)) is not None


@pytest.mark.parametrize("reason", ["insufficient_funds", "card_expired", "invalid_vpa"])
def test_a_settled_outcome_is_not(reason: str) -> None:
    """An empty account is unambiguous. Checking would be a wasted call."""
    assert needs_verification(signal(reason)) is None


def test_nothing_is_verified_without_an_order_to_check() -> None:
    """A verification step that cannot run is worse than none."""
    assert needs_verification(signal(order=None)) is None


# -- what happens then --------------------------------------------------------


def test_a_paid_order_closes_the_case_and_never_retries(store: Store) -> None:
    case = handle_failure(store, signal(), verifier=Settled(), now=NOW)

    assert store.get_case(case.id).state is CaseState.CLOSED_FALSE_FAILURE
    kinds = [r.decision_type for r in store.timeline(case.id)]
    assert "schedule" not in kinds, "a retry was scheduled on a payment already made"
    assert store.pending_actions(case_id=case.id) == []

    stop = next(r for r in store.timeline(case.id) if r.decision_type == "stop")
    assert "FALSE_FAILURE" in stop.rules_fired
    assert "not counted as a recovery" in stop.outcome


def test_an_unpaid_order_lets_recovery_proceed(store: Store) -> None:
    """error_source=bank resolves the documented ambiguity, so the diagnosis is
    confident enough to act on once verification clears it."""
    case = handle_failure(store, signal(source="bank"), verifier=Unpaid(), now=NOW)
    kinds = [r.decision_type for r in store.timeline(case.id)]
    assert "schedule" in kinds
    assert store.get_case(case.id).state is CaseState.OPEN
    assert any("VERIFIED_UNPAID" in r.rules_fired for r in store.timeline(case.id))


def test_an_unresolved_ambiguity_still_holds_after_a_clean_verification(store: Store) -> None:
    """Verification answers "was this paid", not "what went wrong".

    payment_timed_out with error_source=gateway does not select either of the
    two causes Razorpay documents, so confidence lands at 0.4 and the case holds
    even though the order is confirmed unpaid. The two mechanisms are
    independent and both have to clear.
    """
    case = handle_failure(store, signal(source="gateway"), verifier=Unpaid(), now=NOW)
    assert any("VERIFIED_UNPAID" in r.rules_fired for r in store.timeline(case.id))
    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN
    assert "schedule" not in [r.decision_type for r in store.timeline(case.id)]


def test_a_failed_check_holds_rather_than_assuming_unpaid(store: Store) -> None:
    """Not-checked is not the same as not-paid. Assuming otherwise charges
    somebody twice."""
    case = handle_failure(store, signal(), verifier=Broken(), now=NOW)

    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN
    assert "schedule" not in [r.decision_type for r in store.timeline(case.id)]
    stop = next(r for r in store.timeline(case.id) if r.decision_type == "stop")
    assert "VERIFICATION_UNAVAILABLE" in stop.rules_fired


def test_no_verifier_configured_holds_too(store: Store) -> None:
    case = handle_failure(store, signal(), now=NOW)
    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN
    assert "schedule" not in [r.decision_type for r in store.timeline(case.id)]


def test_a_settled_failure_needs_no_verifier_at_all(store: Store) -> None:
    """insufficient_funds proceeds normally without one."""
    case = handle_failure(store, signal("insufficient_funds"), now=NOW)
    assert store.get_case(case.id).state is CaseState.OPEN
    assert "schedule" in [r.decision_type for r in store.timeline(case.id)]


def test_the_reason_for_checking_is_recorded_verbatim(store: Store) -> None:
    """The specification: the reconciliation reasoning is logged verbatim."""
    case = handle_failure(store, signal(), verifier=Unpaid(), now=NOW)
    check = next(r for r in store.timeline(case.id) if r.decision_type == "verification")
    assert "Razorpay documents a capture" in check.inputs["why"]
    assert check.inputs["check"].startswith("whether order")
