"""Which intervention to try, and when it stops being worth trying.

The expected-value gate is split deliberately. One half needs no assumption and
is a fact; the other rests on a success rate nobody has measured. Tests for the
two are separate because the claims are different.
"""

from __future__ import annotations

import pytest

from unhalted.models import DiagnosisClass
from unhalted.shell.ladder import LADDER, RUPEE, Rung, entry_rung, evaluate, next_rung


def rupees(n: int) -> int:
    return n * RUPEE


# -- where a case joins ---------------------------------------------------


def test_a_broken_mandate_skips_the_retries_that_cannot_work() -> None:
    """No number of silent retries fixes an expired card."""
    assert entry_rung(DiagnosisClass.MANDATE_STATE_BROKEN) is Rung.REAUTHORISATION


def test_a_notification_gap_starts_by_notifying() -> None:
    """A silent retry just repeats the failure the customer never heard about."""
    assert entry_rung(DiagnosisClass.NOTIFICATION_GAP) is Rung.NUDGE


def test_recoverable_failures_start_free() -> None:
    for klass in (DiagnosisClass.RECOVERABLE_TECHNICAL, DiagnosisClass.RECOVERABLE_BALANCE):
        assert entry_rung(klass) is Rung.SILENT_RETRY


def test_a_revoked_mandate_has_no_ladder_at_all() -> None:
    assert entry_rung(DiagnosisClass.CUSTOMER_INTENT_REVOKED) is None
    assert entry_rung(DiagnosisClass.UNKNOWN) is None


# -- the half that needs no assumption ------------------------------------


def test_a_rung_costing_more_than_the_stake_is_refused_at_any_success_rate() -> None:
    """A probability cannot exceed 1, so this needs no estimate of anything."""
    d = evaluate(Rung.HUMAN_CALLBACK, rupees(49))
    assert not d.approved
    assert d.rules_fired == ["UNECONOMIC:PROVABLE"]
    assert d.assumption_used is False
    assert "at any success rate" in d.reason
    assert "cost Rs 60" in d.calculation


def test_the_provable_refusal_shows_its_arithmetic() -> None:
    d = evaluate(Rung.VOICE_CALL, rupees(5))
    assert not d.approved
    assert "cost Rs 8" in d.calculation and "stake Rs 5" in d.calculation


def test_a_free_rung_needs_no_justification() -> None:
    d = evaluate(Rung.SILENT_RETRY, rupees(49))
    assert d.approved
    assert d.assumption_used is False


# -- the half that rests on an assumption ---------------------------------


def test_a_marginal_case_is_decided_on_an_assumed_rate_and_says_so() -> None:
    d = evaluate(Rung.VOICE_CALL, rupees(499))
    assert d.approved
    assert d.assumption_used is True, "a decision resting on an estimate must admit it"
    assert "success rate" in d.calculation


def test_a_case_worth_less_than_the_expected_cost_is_refused() -> None:
    d = evaluate(Rung.HUMAN_CALLBACK, rupees(100))
    assert not d.approved
    assert d.rules_fired == ["UNECONOMIC:ASSUMED"]
    assert d.assumption_used is True


def test_a_valuable_customer_justifies_a_deeper_ladder() -> None:
    """Lifetime value raises the stake, so a case refused on the amount alone
    can be worth pursuing for the relationship behind it.

    Deliberately uses an amount that fails the *provable* half of the gate, so
    the test does not depend on an assumed success rate to mean anything.
    """
    without = evaluate(Rung.VOICE_CALL, rupees(5))
    with_ltv = evaluate(Rung.VOICE_CALL, rupees(5), customer_ltv_paise=rupees(18_000))

    assert not without.approved
    assert without.rules_fired == ["UNECONOMIC:PROVABLE"]
    assert with_ltv.approved
    assert "LTV_JUSTIFIED" in with_ltv.rules_fired


def test_the_conditional_gate_is_only_as_good_as_its_assumed_rate() -> None:
    """This is the honest weakness, asserted rather than hidden.

    The specification's own scenario expects Rs 49 to be uneconomic for a voice
    call. At the success rate assumed here it is not: 35% of Rs 49 is Rs 17,
    against a Rs 8 call. Neither answer is wrong — the number nobody has
    measured decides it.

    These rates are merchant policy, not measurements, and this project cannot
    measure them — a generated batch cannot supply real recovery outcomes, so
    reading rates back out of one would be circular. Every decision resting on a
    rate is flagged, and this test exists so nobody mistakes the conditional half
    of the gate for a finding.
    """
    d = evaluate(Rung.VOICE_CALL, rupees(49))
    assert d.assumption_used is True
    assert "success rate" in d.calculation

    from unhalted.shell.ladder import DEFAULT_SUCCESS

    pessimistic = evaluate(
        Rung.VOICE_CALL, rupees(49),
        success_rates={**DEFAULT_SUCCESS, Rung.VOICE_CALL: 0.10},
    )

    assert d.approved and not pessimistic.approved, (
        "the same case flips on the assumed rate alone, which is why the rate "
        "must be measured before any rupee figure derived from it is reported"
    )


# -- what the customer refuses --------------------------------------------


def test_a_refused_channel_is_never_used_however_economic() -> None:
    """"call mat karo" outranks arithmetic."""
    d = evaluate(Rung.VOICE_CALL, rupees(5000), refused_channels=frozenset({"voice"}))
    assert not d.approved
    assert d.rules_fired == ["CHANNEL_REFUSED"]


def test_escalation_steps_over_a_refused_channel() -> None:
    assert next_rung(Rung.REAUTHORISATION) is Rung.VOICE_CALL
    assert next_rung(
        Rung.REAUTHORISATION, refused_channels=frozenset({"voice"})
    ) is Rung.HUMAN_CALLBACK


def test_the_ladder_ends() -> None:
    assert next_rung(Rung.HUMAN_CALLBACK) is None


# -- the ladder itself -----------------------------------------------------


def test_the_ladder_runs_from_free_to_expensive() -> None:
    costs = [LADDER[r].cost_paise for r in sorted(LADDER)]
    assert costs == sorted(costs)
    assert costs[0] == 0


def test_a_silent_retry_is_not_contact() -> None:
    """It survives an opt-out; the others do not."""
    assert LADDER[Rung.SILENT_RETRY].is_contact is False
    for rung in (Rung.NUDGE, Rung.VOICE_CALL, Rung.HUMAN_CALLBACK):
        assert LADDER[rung].is_contact is True


@pytest.mark.parametrize("rung", list(Rung))
def test_every_rung_says_why_it_exists(rung: Rung) -> None:
    assert len(LADDER[rung].why) > 20


# -- the ladder is used, not merely defined --------------------------------


def test_a_broken_mandate_gets_a_reauthorisation_and_no_retry(tmp_path) -> None:
    """Asserted from the agent's entry point. A module nobody calls is
    decoration, which is how the monetary ceilings shipped unenforced."""
    from datetime import datetime

    from unhalted.agent import handle_failure
    from unhalted.models import FailureSignal
    from unhalted.shell.windows import IST
    from unhalted.store import Store

    store = Store(str(tmp_path / "l.db"))
    try:
        signal = FailureSignal(
            payment_id="pay_EXPIRED", customer_ref="cust_l", amount_paise=49900,
            occurred_at=datetime(2026, 9, 20, 14, 0, tzinfo=IST), source="test",
            method="card", error_reason="card_expired", error_source="issuer_bank",
        )
        case = handle_failure(store, signal, now=datetime(2026, 9, 20, 14, 0, tzinfo=IST))

        kinds = [r.decision_type for r in store.timeline(case.id)]
        assert "schedule" not in kinds, "a retry cannot fix an expired card"
        escalation = next(r for r in store.timeline(case.id) if r.decision_type == "escalation")
        assert "rung 3" in escalation.action
        # The lookup key runner.EXECUTORS would need to actually execute
        # this, not Intervention.name's prose — "re-authorisation link" was
        # never a key anything matched, so this action could never run.
        assert [a["kind"] for a in store.pending_actions(case_id=case.id)] == [
            "reauthorisation"
        ]
    finally:
        store.close()


def test_a_notification_gap_nudge_actually_runs_through_run_due(tmp_path, monkeypatch) -> None:
    """The scheduling test above only proves the row looks right. This proves
    the row is *usable* — the same claim `agent.py` made about NUDGE for
    months while `run_due` silently found nothing to claim.

    `kind` had been `Intervention.name` ("message with a pay link"), never a
    key in `runner.EXECUTORS`, and `scheduled_for` had been `None`, which the
    claiming query's `scheduled_for <= now` can never satisfy. Either fault
    alone would have kept this at `claimed == 0` forever; a test asserting
    only the `kind` string, as the one above does, cannot catch that.
    """
    from datetime import datetime
    from unittest.mock import patch

    from unhalted.agent import handle_failure
    from unhalted.models import FailureSignal
    from unhalted.runner import run_due
    from unhalted.shell import paylink
    from unhalted.shell.windows import IST
    from unhalted.store import Store

    store = Store(str(tmp_path / "n.db"))
    try:
        now = datetime(2026, 9, 20, 11, 0, tzinfo=IST)  # inside contact hours
        signal = FailureSignal(
            payment_id="pay_NOAUTH", customer_ref="cust_n", amount_paise=49900,
            occurred_at=now, source="test",
            method="upi", error_reason="authentication_failed", error_source="customer",
        )
        case = handle_failure(store, signal, now=now)

        pending = store.pending_actions(case_id=case.id)
        assert [a["kind"] for a in pending] == ["nudge"]
        assert pending[0]["scheduled_for"] is not None, (
            "an action due at None can never satisfy run_due's `<= now` claim"
        )

        # No real network call to Razorpay for a pay link in a test.
        with patch.object(paylink, "create_payment_link", return_value=None):
            report = run_due(store, now=now)

        assert report.claimed == 1, "the nudge row must actually be claimable"
        assert report.done == 1, "and the registered nudge executor must run it"
        assert store.pending_actions(case_id=case.id) == []
    finally:
        store.close()


def test_an_uneconomic_case_is_closed_with_its_arithmetic_recorded(tmp_path) -> None:
    """The specification: the early termination is logged with the EV calculation."""
    from datetime import datetime

    from unhalted.agent import handle_failure
    from unhalted.models import CaseState, FailureSignal
    from unhalted.shell.windows import IST
    from unhalted.store import Store

    store = Store(str(tmp_path / "u.db"))
    try:
        # Rs 1 owed, and the only path is a Rs 2 re-authorisation link.
        signal = FailureSignal(
            payment_id="pay_TINY", customer_ref="cust_t", amount_paise=100,
            occurred_at=datetime(2026, 9, 20, 14, 0, tzinfo=IST), source="test",
            method="card", error_reason="card_expired", error_source="issuer_bank",
        )
        case = handle_failure(store, signal, now=datetime(2026, 9, 20, 14, 0, tzinfo=IST))

        assert store.get_case(case.id).state is CaseState.UNRECOVERED
        escalation = next(r for r in store.timeline(case.id) if r.decision_type == "escalation")
        assert "uneconomic" in escalation.action
        assert escalation.inputs["calculation"], "the arithmetic must be in the record"
        assert escalation.inputs["rested_on_an_assumed_rate"] is False
        assert store.pending_actions(case_id=case.id) == []
    finally:
        store.close()


def test_a_merchant_can_supply_their_own_rates() -> None:
    """The rates belong to whoever has the history, which is not this project."""
    from unhalted.shell.ladder import DEFAULT_SUCCESS

    optimistic = evaluate(
        Rung.HUMAN_CALLBACK, rupees(200),
        success_rates={**DEFAULT_SUCCESS, Rung.HUMAN_CALLBACK: 0.95},
    )
    assert optimistic.approved
    assert "merchant's" in optimistic.calculation


def test_the_default_rate_is_labelled_as_unmeasured_in_the_record() -> None:
    d = evaluate(Rung.VOICE_CALL, rupees(499))
    assert "unmeasured" in d.calculation
