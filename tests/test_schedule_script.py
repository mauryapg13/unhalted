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

from unhalted.agent import apply_stop, handle_failure, mark_recovered
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
def held_case(tmp_path, schedule):
    store = Store(str(tmp_path / "schedule.db"))
    signal = FailureSignal(
        payment_id="pay_SCHED", customer_ref="cust_sched", amount_paise=49900,
        occurred_at=NOW, source="test", method="card",
        error_reason="payment_risk_check_failed", error_source="issuer",
    )
    case = handle_failure(store, signal, now=NOW)
    store.set_state(case.id, CaseState.HELD_FOR_HUMAN)
    # `payment_risk_check_failed` lands at UNKNOWN, 0.0 confidence, which
    # auto-holds *and* writes its own real `stop` record ("awaiting human
    # review") on the way — exactly the shape `audit_lines` now surfaces
    # (see the STOPPED fix below). Real, but incidental to what each test
    # below adds on top of an already-held case; primed into `seen` once
    # here so a test's own assertions see only what it added.
    seen: set[tuple[str, str, str]] = set()
    schedule.audit_lines(store, seen, now=NOW)
    yield store, case, seen
    store.close()


def test_a_reviewer_decision_is_reported(schedule, held_case) -> None:
    store, case, seen = held_case
    store.record(
        AuditRecord(
            case_id=case.id, at=NOW, decision_type="human-review",
            action="approved", inputs={}, rules_fired=["HUMAN_GATE"],
            human_actor="priya", outcome="decided by priya",
        )
    )
    lines = schedule.audit_lines(store, seen, now=NOW)
    assert len(lines) == 1
    assert "REVIEWED" in lines[0].text
    assert "approved" in lines[0].text
    assert "priya" in lines[0].text


def test_the_same_decision_is_not_reported_twice(schedule, held_case) -> None:
    store, case, seen = held_case
    store.record(
        AuditRecord(
            case_id=case.id, at=NOW, decision_type="human-review",
            action="rejected", inputs={}, rules_fired=["HUMAN_GATE"],
            human_actor="priya", outcome="decided by priya",
        )
    )
    first = schedule.audit_lines(store, seen, now=NOW)
    second = schedule.audit_lines(store, seen, now=NOW)
    assert len(first) == 1
    assert second == []


def test_a_recovery_is_reported(schedule, held_case) -> None:
    """A customer paying through the recovery link closes the case in the
    store; without this branch, nothing here said so."""
    store, case, seen = held_case
    mark_recovered(store, case.id, payment_id="pay_XYZ", amount_paise=49900, now=NOW)

    lines = schedule.audit_lines(store, seen, now=NOW)
    assert len(lines) == 1
    assert "RECOVERED" in lines[0].text
    assert "paid via recovery link" in lines[0].text


def test_a_reply_is_reported(schedule, held_case) -> None:
    """The cause, not just its effect — a reply is what leads to a stop or a
    cancellation, and printed nowhere until now the log started at whatever
    it caused instead."""
    store, case, seen = held_case
    store.record(
        AuditRecord(
            case_id=case.id, at=NOW, decision_type="reply",
            action="parsed", inputs={"reply": "stop"}, rules_fired=["STOP_RULE:OPT_OUT"],
            outcome="they asked not to be contacted",
        )
    )
    lines = schedule.audit_lines(store, seen, now=NOW)
    assert len(lines) == 1
    assert "REPLY" in lines[0].text
    assert "they asked not to be contacted" in lines[0].text


def test_a_stop_that_cancels_nothing_still_shows(schedule, held_case) -> None:
    """`apply_stop` (and a reply's `needs_human` path) write a `stop` record
    regardless of whether anything was pending to cancel. `cancellation_event`
    only ever fires from a *cancelled action row*, so a stop landing on a
    case with nothing left pending — a nudge that had already delivered, say
    — cancelled zero rows and produced zero lines here, even though the case
    really did just stop. `held_case` is itself proof this happens for real:
    its own UNKNOWN diagnosis auto-holds with nothing ever scheduled."""
    store, case, seen = held_case
    assert store.pending_actions(case_id=case.id) == [], "nothing here to cancel"

    cancelled = apply_stop(
        store, "OPT_OUT", case_id=case.id, customer_ref=case.customer_ref, now=NOW,
    )
    assert cancelled == 0, "the exact condition that used to hide this entirely"

    lines = schedule.audit_lines(store, seen, now=NOW)
    assert len(lines) == 1
    assert "STOPPED" in lines[0].text


def test_a_backfilled_execution_shows_when_it_really_happened(schedule, held_case) -> None:
    """A worker running under `--at`, or a scheduler started after the fact,
    means the record's own time and the poll tick that first noticed it can
    genuinely differ. Stamping the line with the poll tick instead of
    `record.at` printed a rehearsed 08:00 execution as if it happened at
    whatever second this viewer happened to be watching — which for anything
    outside contact hours reads as a violation that never occurred.
    """
    store, case, seen = held_case
    real_time = datetime(2026, 9, 3, 23, 0, tzinfo=IST)  # outside contact hours
    store.record(
        AuditRecord(
            case_id=case.id, at=NOW, decision_type="execution",
            action="nudge: done", inputs={}, rules_fired=["RUNNER"],
            outcome="message delivered",
        )
    )
    lines = schedule.audit_lines(store, seen, now=real_time)
    assert len(lines) == 1
    assert NOW.strftime("%H:%M:%S") in lines[0].text
    assert real_time.strftime("%H:%M:%S") not in lines[0].text


def test_an_escalation_reads_as_escalated_not_cancelled(schedule) -> None:
    action = {"id": 1, "case_id": "CASE-ABC", "kind": "retry", "cancel_reason": "HELD_FOR_HUMAN"}
    line = schedule.cancellation_event(action, now=NOW)
    assert "ESCALATED" in line.text
    assert "held for a human" in line.text
    assert "CANCELLED" not in line.text


def test_every_held_for_human_stop_rule_also_reads_as_escalated(schedule) -> None:
    for reason in ("DISPUTE", "DISTRESS", "CHARGEBACK", "REG_HOLD"):
        action = {"id": 1, "case_id": "CASE-ABC", "kind": "retry", "cancel_reason": reason}
        line = schedule.cancellation_event(action, now=NOW)
        assert "ESCALATED" in line.text, f"{reason} should read as an escalation"


def test_a_routine_cancellation_still_reads_as_cancelled(schedule) -> None:
    """REALIGNED, RETRY_CAP and the like are not escalations — a promise-to-pay
    changing the schedule is not the same event as a case needing a person."""
    action = {"id": 1, "case_id": "CASE-ABC", "kind": "retry", "cancel_reason": "REALIGNED"}
    line = schedule.cancellation_event(action, now=NOW)
    assert "CANCELLED" in line.text
    assert "ESCALATED" not in line.text


def test_a_recovered_cancellation_is_not_reported_twice(schedule) -> None:
    """RECOVERED already gets its own distinct event from audit_lines; this
    branch would otherwise print the same real thing a second time."""
    action = {"id": 1, "case_id": "CASE-ABC", "kind": "retry", "cancel_reason": "RECOVERED"}
    assert schedule.cancellation_event(action, now=NOW) is None


def test_an_unattributed_decision_still_says_someone_decided(schedule, held_case) -> None:
    """`human_actor` is optional on the model; the log must not go silent for it."""
    store, case, seen = held_case
    store.record(
        AuditRecord(
            case_id=case.id, at=NOW, decision_type="human-review",
            action="approved", inputs={}, rules_fired=["HUMAN_GATE"],
            outcome="decided",
        )
    )
    lines = schedule.audit_lines(store, seen, now=NOW)
    assert "a reviewer" in lines[0].text


def test_a_batch_of_events_prints_in_the_order_they_happened(schedule) -> None:
    """The failure this guards: a cold read.

    Live, events are gathered cancellations-first and each poll's batch spans
    a second or two, so the grouping is invisible. Opened against an existing
    database the whole history arrives in one batch, and that grouping used to
    override chronology — a cancellation from 12:10 printed above the delivery
    from 12:09 that caused it. Sequence is the argument this log exists to
    make, so the batch is ordered on the times its events actually carry.
    """
    delivered = datetime(2026, 9, 3, 12, 9, 0, tzinfo=IST)
    replied = datetime(2026, 9, 3, 12, 10, 0, tzinfo=IST)

    batch = [
        schedule.event("CANCELLED", "CASE-ABC", "retry  OPT_OUT", now=replied),
        schedule.event("EXECUTED", "CASE-ABC", "message delivered", now=delivered),
        schedule.event("REPLY", "CASE-ABC", "they asked not to be contacted", now=replied),
    ]

    assert [ln.at for ln in sorted(batch)] == [delivered, replied, replied]


def test_events_sharing_one_second_read_cause_before_effect(schedule) -> None:
    """A reply is read, the stop it triggers fires, and the actions that stop
    cancels are cancelled — all inside the same second. Printed in gathering
    order that reads backwards, which argues the opposite of what happened."""
    at = datetime(2026, 9, 3, 12, 10, 0, tzinfo=IST)

    batch = [
        schedule.event("CANCELLED", "CASE-ABC", "retry  OPT_OUT", now=at),
        schedule.event("STOPPED", "CASE-ABC", "suppress all automated contact", now=at),
        schedule.event("REPLY", "CASE-ABC", "they asked not to be contacted", now=at),
    ]

    ordered = [ln.text for ln in sorted(batch)]
    assert "REPLY" in ordered[0]
    assert "STOPPED" in ordered[1]
    assert "CANCELLED" in ordered[2]
