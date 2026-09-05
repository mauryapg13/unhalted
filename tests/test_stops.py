"""The stop rules, and that nothing overrides them.

A stop is not a recommendation weighed against others. It is a fact about the
case that ends automated action, and no confidence from any component lifts it.
These tests are the part of the suite that would go red if that ever stopped
being true.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from unhalted.agent import apply_stop
from unhalted.models import CaseState, FailureSignal
from unhalted.shell import stops
from unhalted.shell.windows import IST
from unhalted.store import Store

SPEC_CODES = {
    "REVOKED", "OPT_OUT", "DISPUTE", "DISTRESS", "RETRY_CAP",
    "LADDER_END", "CHARGEBACK", "MERCHANT_PAUSE", "REG_HOLD",
}


def signal(payment_id: str = "pay_STOP0001", customer: str = "cust_stop") -> FailureSignal:
    return FailureSignal(
        payment_id=payment_id,
        customer_ref=customer,
        amount_paise=49900,
        occurred_at=datetime(2026, 9, 1, 14, 0, tzinfo=IST),
        source="test",
        method="card",
        error_reason="insufficient_funds",
        error_source="customer",
    )


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "stops.db"))
    yield s
    s.close()


# -- the rules themselves -----------------------------------------------------


def test_every_stop_the_specification_names_exists() -> None:
    assert set(stops.RULES) == SPEC_CODES


def test_each_rule_carries_the_code_the_audit_trail_records() -> None:
    for code, rule in stops.RULES.items():
        assert rule.code == code
        assert stops.audit_code(code) == f"STOP_RULE:{code}"


def test_an_unknown_stop_code_is_refused_rather_than_ignored() -> None:
    """A typo must not silently disable a stop."""
    with pytest.raises(KeyError, match="unknown stop rule"):
        stops.rule("REVOKD")


def test_every_rule_says_why_it_exists() -> None:
    for rule in stops.RULES.values():
        assert rule.why and len(rule.why) > 20


def test_the_stops_that_must_act_within_a_minute_do() -> None:
    """The specification's SLA column, which is a promise to the customer."""
    for code in ("REVOKED", "OPT_OUT", "DISPUTE", "DISTRESS"):
        assert stops.rule(code).sla is not None
        assert stops.rule(code).sla.total_seconds() <= 60


def test_contact_is_suppressed_by_every_stop_that_should() -> None:
    for code in ("REVOKED", "OPT_OUT", "DISPUTE", "DISTRESS", "CHARGEBACK",
                 "MERCHANT_PAUSE", "REG_HOLD", "LADDER_END"):
        assert stops.rule(code).suppresses_contact, code
    # A retry cap ends retries and enters the ladder; it does not silence us.
    assert not stops.rule("RETRY_CAP").suppresses_contact


# -- firing them --------------------------------------------------------------


def test_a_revocation_cancels_every_pending_action_at_once(store: Store) -> None:
    """The specification's scenario, literally: one retry, two nudges, one call."""
    case = store.open_case(signal())
    now = datetime(2026, 9, 1, 14, 0, tzinfo=IST)
    for kind in ("retry", "nudge", "nudge", "voice-callback"):
        store.schedule_action(case.id, case.customer_ref, kind, now, now)

    assert len(store.pending_actions(customer_ref=case.customer_ref)) == 4

    cancelled = apply_stop(
        store, "REVOKED", case_id=case.id, customer_ref=case.customer_ref, now=now
    )

    assert cancelled == 4
    assert store.pending_actions(customer_ref=case.customer_ref) == []
    assert store.get_case(case.id).state is CaseState.CLOSED_REVOKED


def test_a_customer_scoped_stop_reaches_the_customer_s_other_cases(store: Store) -> None:
    """A chargeback freezes the customer, not just the case it arrived on."""
    now = datetime(2026, 9, 1, 14, 0, tzinfo=IST)
    first = store.open_case(signal("pay_A", "cust_shared"))
    second = store.open_case(signal("pay_B", "cust_shared"))
    store.schedule_action(first.id, "cust_shared", "retry", now, now)
    store.schedule_action(second.id, "cust_shared", "nudge", now, now)

    cancelled = apply_stop(
        store, "CHARGEBACK", case_id=first.id, customer_ref="cust_shared", now=now
    )

    assert cancelled == 2
    assert store.pending_actions(customer_ref="cust_shared") == []


def test_a_case_scoped_stop_leaves_other_cases_alone(store: Store) -> None:
    now = datetime(2026, 9, 1, 14, 0, tzinfo=IST)
    mine = store.open_case(signal("pay_C", "cust_two"))
    other = store.open_case(signal("pay_D", "cust_two"))
    store.schedule_action(mine.id, "cust_two", "retry", now, now)
    store.schedule_action(other.id, "cust_two", "retry", now, now)

    cancelled = apply_stop(
        store, "DISTRESS", case_id=mine.id, customer_ref="cust_two", now=now
    )

    assert cancelled == 1
    assert len(store.pending_actions(case_id=other.id)) == 1


def test_a_stop_is_written_to_the_audit_trail_with_its_code(store: Store) -> None:
    now = datetime(2026, 9, 1, 14, 0, tzinfo=IST)
    case = store.open_case(signal("pay_E", "cust_audit"))
    apply_stop(store, "OPT_OUT", case_id=case.id, customer_ref="cust_audit",
               detail="reply: mujhe dobara message mat karna", now=now)

    stop_records = [r for r in store.timeline(case.id) if r.decision_type == "stop"]
    assert len(stop_records) == 1
    assert "STOP_RULE:OPT_OUT" in stop_records[0].rules_fired
    assert "mujhe dobara message mat karna" in stop_records[0].inputs["detail"]


def test_distress_routes_the_case_to_a_human(store: Store) -> None:
    now = datetime(2026, 9, 1, 14, 0, tzinfo=IST)
    case = store.open_case(signal("pay_F", "cust_distress"))
    apply_stop(store, "DISTRESS", case_id=case.id, customer_ref="cust_distress", now=now)
    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN


def test_cancelling_with_no_scope_is_refused(store: Store) -> None:
    """A cancel with neither scope would cancel everything for everyone."""
    with pytest.raises(ValueError, match="needs a customer_ref or a case_id"):
        store.cancel_pending("OOPS")


# -- the ceilings are checked before a debit is scheduled, not merely defined --


def big_signal(amount_paise: int, method: str = "card", mandate_max: int | None = None):
    return FailureSignal(
        payment_id=f"pay_BIG{amount_paise}",
        customer_ref="cust_big",
        amount_paise=amount_paise,
        occurred_at=datetime(2026, 9, 1, 14, 0, tzinfo=IST),
        source="test",
        method=method,
        mandate_max_paise=mandate_max,
        error_reason="insufficient_funds",
        error_source="customer",
    )


def test_a_debit_above_the_card_ceiling_is_never_scheduled(store: Store) -> None:
    """The check has to run in the path, not only in its own tests."""
    from unhalted.agent import handle_failure

    case = handle_failure(store, big_signal(20_000 * 100), now=datetime(2026, 9, 1, 14, 0, tzinfo=IST))
    kinds = [r.decision_type for r in store.timeline(case.id)]
    schedule = [r for r in store.timeline(case.id) if r.decision_type == "schedule"]

    assert kinds == ["ingest", "diagnosis", "escalation", "schedule"]
    # A balance failure asks the customer for a date and arms a fallback
    # retry behind it; the ceiling refuses that fallback for the same reason
    # it refuses any other retry, and says so in the same terms.
    assert schedule[0].action == "fallback retry refused on amount"
    assert schedule[0].rules_fired == ["LIMIT:WOULD_FAIL"]
    assert "fails automatically" in schedule[0].outcome


def test_a_debit_above_the_mandate_ceiling_is_never_scheduled(store: Store) -> None:
    """Consent outranks feasibility: ₹500 is fine for a card and not fine here."""
    from unhalted.agent import handle_failure

    case = handle_failure(
        store,
        big_signal(500 * 100, mandate_max=200 * 100),
        now=datetime(2026, 9, 1, 14, 0, tzinfo=IST),
    )
    schedule = next(r for r in store.timeline(case.id) if r.decision_type == "schedule")
    assert schedule.rules_fired == ["LIMIT:EXCEEDS_MANDATE"]
    assert "never agreed" in schedule.outcome


def test_a_upi_debit_needing_authorisation_is_not_retried_blindly(store: Store) -> None:
    """It has not failed. Retrying it is the wrong response."""
    from unhalted.agent import handle_failure

    case = handle_failure(
        store, big_signal(20_000 * 100, method="upi"),
        now=datetime(2026, 9, 1, 14, 0, tzinfo=IST),
    )
    schedule = next(r for r in store.timeline(case.id) if r.decision_type == "schedule")
    assert schedule.rules_fired == ["LIMIT:NEEDS_ADDITIONAL_AUTH"]
    assert "waiting for a person" in schedule.outcome


def test_a_permissible_amount_still_schedules_a_retry(store: Store) -> None:
    from unhalted.agent import handle_failure

    case = handle_failure(store, big_signal(499 * 100), now=datetime(2026, 9, 1, 14, 0, tzinfo=IST))
    schedule = next(r for r in store.timeline(case.id) if r.decision_type == "schedule")
    # "fallback" because a balance failure asks the customer for a date first
    # — the retry behind that question is still a real, scheduled retry.
    assert schedule.action.startswith("fallback retry at")


# -- which stops leave a case needing a person --------------------------------


def test_the_stops_that_leave_an_unanswered_question_hold_for_a_human() -> None:
    """Halting is not resolving.

    A dispute is a factual claim about the customer's money that nothing here
    can settle — verification is against transaction history and any refund
    needs human approval. A chargeback is already formal. Distress needs a
    person by its nature, and a regulatory advisory comes from someone with
    more authority than this system.

    A stop that halts without holding abandons the case silently, which is
    worse than not halting.
    """
    for code in ("DISPUTE", "CHARGEBACK", "DISTRESS", "REG_HOLD"):
        assert stops.rule(code).terminal_state is CaseState.HELD_FOR_HUMAN, code


def test_the_stops_with_nothing_left_to_decide_do_not_hold() -> None:
    """A revoked mandate is closed, not pending. An opt-out needs no ruling."""
    assert stops.rule("REVOKED").terminal_state is CaseState.CLOSED_REVOKED
    assert stops.rule("LADDER_END").terminal_state is CaseState.UNRECOVERED
    for code in ("OPT_OUT", "RETRY_CAP", "MERCHANT_PAUSE"):
        assert stops.rule(code).terminal_state is not CaseState.HELD_FOR_HUMAN, code


def test_a_dispute_holds_the_case_for_a_person(store: Store) -> None:
    now = datetime(2026, 9, 1, 14, 0, tzinfo=IST)
    case = store.open_case(signal("pay_DISP", "cust_disp"))
    apply_stop(store, "DISPUTE", case_id=case.id, customer_ref="cust_disp", now=now)
    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN


def test_a_revocation_cancels_the_retry_the_agent_scheduled_itself(store: Store) -> None:
    """Found by the CLI printing "pending 0" beside a scheduled retry.

    The audit trail said a retry was scheduled and nothing tracked it as
    pending, so a stop had nothing to cancel. A customer revoking their mandate
    would have been charged anyway. The audit records what was decided; the
    pending table is what makes it cancellable, and both are needed.
    """
    from unhalted.agent import handle_failure

    now = datetime(2026, 9, 3, 14, 0, tzinfo=IST)
    case = handle_failure(store, signal("pay_REVOKE", "cust_revoke"), now=now)

    pending = store.pending_actions(case_id=case.id)
    # A balance failure schedules both halves at once: the question asking
    # when to try, and the fallback retry that runs only if nobody answers.
    # A revocation has to reach both — leaving either armed would contact or
    # charge somebody who has withdrawn permission.
    assert [a["kind"] for a in pending] == ["nudge", "retry"], (
        "both scheduled actions must be trackable"
    )

    cancelled = apply_stop(
        store, "REVOKED", case_id=case.id, customer_ref="cust_revoke", now=now
    )
    assert cancelled == 2
    assert store.pending_actions(case_id=case.id) == []


def test_a_cancellation_records_when_it_happened(store: Store) -> None:
    """A queue row has to carry its own cancellation time.

    Without it the scheduler view had no time to show for a cancelled action
    and substituted its own poll clock, so opening the log against an existing
    database dated every historical stop to the second the viewer started.
    An append-only log whose whole argument is *this was cancelled before that
    could run* cannot source its timestamps from whoever happens to be
    watching.
    """
    case = store.open_case(signal("pay_STAMP", "cust_stamp"))
    scheduled = datetime(2026, 9, 1, 14, 0, tzinfo=IST)
    store.schedule_action(case.id, case.customer_ref, "retry", scheduled, scheduled)

    stopped_at = datetime(2026, 9, 1, 16, 30, tzinfo=IST)
    apply_stop(
        store, "OPT_OUT", case_id=case.id, customer_ref=case.customer_ref, now=stopped_at
    )

    [row] = store.actions(state="cancelled", case_id=case.id)
    assert datetime.fromisoformat(row["cancelled_at"]) == stopped_at, (
        "the cancellation is stamped when it happened, not when it is read back"
    )


def test_an_opt_out_that_also_asks_to_cancel_still_reaches_a_person(store: Store) -> None:
    """Two asks in one reply, not two readings of one.

    "Mujhe nhi chaiye. cancel kro" parsed as cancellation-request at 0.95 and
    opt-out at 0.50. Opt-out clears its bar at 0.50 deliberately — missing a
    stop costs a compliance failure — and `decide` returns at the first branch
    that matches, so the weak signal was fired and the strong one discarded.
    Contact was suppressed, nobody was told to cancel anything, and the mandate
    went on billing somebody the system had just promised never to contact
    again. The one thing they actually asked for was the one thing that did not
    happen.
    """
    from unhalted import agent as agent_module
    from unhalted.agent import handle_reply
    from unhalted.models import DetectedIntent, Intent, ParsedReply, Sentiment

    case = store.open_case(signal("pay_CANCEL", "cust_cancel"))
    now = datetime(2026, 9, 6, 10, 0, tzinfo=IST)

    both = ParsedReply(
        raw="Mujhe nhi chaiye. cancel kro",
        intents=[
            DetectedIntent(type=Intent.CANCELLATION_REQUEST, confidence=0.95,
                           evidence="cancel kro"),
            DetectedIntent(type=Intent.OPT_OUT, confidence=0.50,
                           evidence="Mujhe nhi chaiye"),
        ],
        sentiment=Sentiment.NEUTRAL,
    )
    original = agent_module.parse_reply
    agent_module.parse_reply = lambda text, context="": both
    try:
        _, outcome = handle_reply(store, case, both.raw, now=now)
    finally:
        agent_module.parse_reply = original

    assert outcome.stop_code == "OPT_OUT", "the stop still wins on contact"
    assert "CANCELLATION_REQUESTED" in outcome.rules_fired, (
        "and the cancellation still has to reach a person"
    )
    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN, (
        "an opt-out carries no terminal state, so nothing else would hold this case"
    )
    assert store.contact_suppression(customer_ref="cust_cancel") is not None, (
        "and they are still not contacted"
    )
