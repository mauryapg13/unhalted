"""The scheduler terminal must show what a reviewer decided, not just that a
case was cancelled into their queue.

`audit_lines` used to filter on `decision_type == "execution"` only, so
`record_decision`'s "human-review" records — approved, rejected, reclassified
— were silently dropped. The log stopped at the cancellation that sent a case
to a person and never said what the person then did about it.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime

import pytest

from unhalted.agent import handle_failure, mark_recovered
from unhalted.models import AuditRecord, CaseState, FailureSignal
from unhalted.shell.windows import IST
from unhalted.store import Store

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "schedule.py"
NOW = datetime(2026, 9, 3, 11, 0, tzinfo=IST)


@pytest.fixture(scope="module")
def schedule():
    spec = importlib.util.spec_from_file_location("schedule_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def held_case(tmp_path):
    store = Store(str(tmp_path / "schedule.db"))
    signal = FailureSignal(
        payment_id="pay_SCHED", customer_ref="cust_sched", amount_paise=49900,
        occurred_at=NOW, source="test", method="card",
        error_reason="payment_risk_check_failed", error_source="issuer",
    )
    case = handle_failure(store, signal, now=NOW)
    store.set_state(case.id, CaseState.HELD_FOR_HUMAN)
    yield store, case
    store.close()


def test_a_reviewer_decision_is_reported(schedule, held_case) -> None:
    store, case = held_case
    store.record(
        AuditRecord(
            case_id=case.id, at=NOW, decision_type="human-review",
            action="approved", inputs={}, rules_fired=["HUMAN_GATE"],
            human_actor="priya", outcome="decided by priya",
        )
    )
    lines = schedule.audit_lines(store, set(), now=NOW)
    assert len(lines) == 1
    assert "REVIEWED" in lines[0]
    assert "approved" in lines[0]
    assert "priya" in lines[0]


def test_the_same_decision_is_not_reported_twice(schedule, held_case) -> None:
    store, case = held_case
    store.record(
        AuditRecord(
            case_id=case.id, at=NOW, decision_type="human-review",
            action="rejected", inputs={}, rules_fired=["HUMAN_GATE"],
            human_actor="priya", outcome="decided by priya",
        )
    )
    seen: set[tuple[str, str, str]] = set()
    first = schedule.audit_lines(store, seen, now=NOW)
    second = schedule.audit_lines(store, seen, now=NOW)
    assert len(first) == 1
    assert second == []


def test_a_recovery_is_reported(schedule, held_case) -> None:
    """A customer paying through the recovery link closes the case in the
    store; without this branch, nothing here said so."""
    store, case = held_case
    mark_recovered(store, case.id, payment_id="pay_XYZ", amount_paise=49900, now=NOW)

    lines = schedule.audit_lines(store, set(), now=NOW)
    assert len(lines) == 1
    assert "RECOVERED" in lines[0]
    assert "paid via recovery link" in lines[0]


def test_an_escalation_reads_as_escalated_not_cancelled(schedule) -> None:
    action = {"id": 1, "case_id": "CASE-ABC", "kind": "retry", "cancel_reason": "HELD_FOR_HUMAN"}
    line = schedule.cancellation_event(action, now=NOW)
    assert "ESCALATED" in line
    assert "held for a human" in line
    assert "CANCELLED" not in line


def test_every_held_for_human_stop_rule_also_reads_as_escalated(schedule) -> None:
    for reason in ("DISPUTE", "DISTRESS", "CHARGEBACK", "REG_HOLD"):
        action = {"id": 1, "case_id": "CASE-ABC", "kind": "retry", "cancel_reason": reason}
        line = schedule.cancellation_event(action, now=NOW)
        assert "ESCALATED" in line, f"{reason} should read as an escalation"


def test_a_routine_cancellation_still_reads_as_cancelled(schedule) -> None:
    """REALIGNED, RETRY_CAP and the like are not escalations — a promise-to-pay
    changing the schedule is not the same event as a case needing a person."""
    action = {"id": 1, "case_id": "CASE-ABC", "kind": "retry", "cancel_reason": "REALIGNED"}
    line = schedule.cancellation_event(action, now=NOW)
    assert "CANCELLED" in line
    assert "ESCALATED" not in line


def test_a_recovered_cancellation_is_not_reported_twice(schedule) -> None:
    """RECOVERED already gets its own distinct event from audit_lines; this
    branch would otherwise print the same real thing a second time."""
    action = {"id": 1, "case_id": "CASE-ABC", "kind": "retry", "cancel_reason": "RECOVERED"}
    assert schedule.cancellation_event(action, now=NOW) is None


def test_an_unattributed_decision_still_says_someone_decided(schedule, held_case) -> None:
    """`human_actor` is optional on the model; the log must not go silent for it."""
    store, case = held_case
    store.record(
        AuditRecord(
            case_id=case.id, at=NOW, decision_type="human-review",
            action="approved", inputs={}, rules_fired=["HUMAN_GATE"],
            outcome="decided",
        )
    )
    lines = schedule.audit_lines(store, set(), now=NOW)
    assert "a reviewer" in lines[0]
