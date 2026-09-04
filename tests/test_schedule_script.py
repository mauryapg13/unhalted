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

from unhalted.agent import handle_failure
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
