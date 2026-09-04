"""A reviewer's decision must resume recovery, not just record it.

`record_decision` used to only flip a held case's state — nothing rescheduled
the retry it was blocking, so an approved case sat OPEN with no pending action
forever, until the customer happened to write in again. These test the fix:
`resume_after_review` re-arms a retry through the same NPCI-banded, cap-checked
path everything else uses.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from unhalted.agent import handle_failure, resume_after_review
from unhalted.models import CaseState, FailureSignal
from unhalted.shell.scheduler import RETRY_CAP
from unhalted.shell.windows import IST
from unhalted.store import Store

NOW = datetime(2026, 9, 4, 11, 0, tzinfo=IST)


@pytest.fixture
def held_case(tmp_path):
    store = Store(str(tmp_path / "resume.db"))

    def make(method: str) -> tuple[Store, object]:
        signal = FailureSignal(
            payment_id=f"pay_RESUME_{method}", customer_ref="cust_resume", amount_paise=49900,
            occurred_at=NOW, source="test", method=method,
            error_reason="payment_risk_check_failed", error_source="issuer",
        )
        case = handle_failure(store, signal, now=NOW)
        store.set_state(case.id, CaseState.HELD_FOR_HUMAN)
        store.cancel_pending("DISPUTE", case_id=case.id)
        return store, store.get_case(case.id)

    yield make
    store.close()


def test_approving_re_arms_a_retry(held_case) -> None:
    store, case = held_case("card")
    assert store.pending_actions(case_id=case.id) == [], "nothing should be scheduled while held"

    decision = resume_after_review(store, case, now=NOW)

    assert decision.accepted
    pending = store.pending_actions(case_id=case.id)
    assert len(pending) == 1
    assert pending[0]["kind"] == "retry"


def test_it_is_recorded_in_the_audit_trail(held_case) -> None:
    store, case = held_case("card")
    resume_after_review(store, case, now=NOW)

    schedules = [r for r in store.timeline(case.id) if r.decision_type == "schedule"]
    assert any("re-armed after review" in r.action for r in schedules)


def test_a_upi_retry_still_honours_npci_bands(held_case) -> None:
    """Re-arming must not be a side door around the band rule the ordinary
    schedule path enforces — issue #30's fix has to reach this path too."""
    store, case = held_case("upi")
    # 11:00 IST sits inside NPCI's 10:00-13:00 restricted band.
    decision = resume_after_review(store, case, now=NOW)

    assert decision.accepted
    assert decision.scheduled_for != NOW, "a UPI retry inside a restricted band must be moved"
    assert any("WINDOW_VIOLATION" in r for r in decision.rules_fired)


def test_a_card_retry_is_not_moved_for_a_upi_only_rule(held_case) -> None:
    store, case = held_case("card")
    decision = resume_after_review(store, case, now=NOW)

    assert decision.scheduled_for == NOW
    assert not any("WINDOW_VIOLATION" in r for r in decision.rules_fired)


def test_a_case_that_already_exhausted_its_cycle_is_not_re_armed(held_case) -> None:
    store, case = held_case("card")
    exhausted = case.model_copy(update={"retry_count": RETRY_CAP})

    decision = resume_after_review(store, exhausted, now=NOW)

    assert not decision.accepted
    assert not any(a["kind"] == "retry" for a in store.pending_actions(case_id=case.id)), (
        "the cap is NPCI's, and a reviewer approving does not raise it"
    )


def test_an_exhausted_cycle_escalates_rather_than_going_quiet(held_case) -> None:
    """Refused is not the same as abandoned.

    A reviewer clearing a held case whose retries are spent used to get one
    audit line saying "refused" and a case with nothing pending and nobody
    told — the omission this ladder exists to prevent. The rungs above are
    absent on this deployment, so the fallback is the one thing that can
    still recover the money without a debit adapter: a payable link, with
    the reason it is arriving.
    """
    store, case = held_case("card")
    exhausted = case.model_copy(update={"retry_count": RETRY_CAP})

    resume_after_review(store, exhausted, now=NOW)

    pending = store.pending_actions(case_id=case.id)
    assert [a["kind"] for a in pending] == ["nudge"]
    assert pending[0]["variant"] == "exhausted"

    escalation = next(
        r for r in store.timeline(case.id)
        if "ESCALATED_AFTER_CAP" in r.rules_fired
    )
    assert "retries spent" in escalation.action
