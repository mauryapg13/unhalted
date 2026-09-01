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
