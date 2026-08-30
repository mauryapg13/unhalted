"""The control loop.

The agent is this loop, not the model. It holds case state, chooses the next
action from a bounded set, and records every decision. At C2 the loop is narrow:
open a case, diagnose it, schedule a retry if the diagnosis warrants one. Each
later checkpoint widens the action set without changing the shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from unhalted.core.diagnose import diagnose
from unhalted.models import AuditRecord, Case, CaseState, DiagnosisClass, FailureSignal
from unhalted.shell import windows
from unhalted.shell.scheduler import schedule_retry
from unhalted.store import Store

#: Classes a silent retry can plausibly fix. Anything else needs a different rung.
RETRYABLE = {
    DiagnosisClass.RECOVERABLE_TECHNICAL,
    DiagnosisClass.RECOVERABLE_BALANCE,
}


def handle_failure(store: Store, signal: FailureSignal, *, now: datetime | None = None) -> Case:
    """Take one failed debit from signal to scheduled next action."""
    now = windows.as_ist(now or datetime.now(tz=windows.IST))
    case = store.open_case(signal)

    store.record(
        AuditRecord(
            case_id=case.id,
            at=now,
            decision_type="ingest",
            action="case-opened",
            inputs={
                "payment_id": signal.payment_id,
                "error_reason": signal.error_reason,
                "error_source": signal.error_source,
                "error_step": signal.error_step,
                "amount_paise": signal.amount_paise,
                "signal_source": signal.source,
            },
            outcome=case.id,
        )
    )

    diagnosis = diagnose(signal)
    store.record_diagnosis(case.id, diagnosis, now)
    store.record(
        AuditRecord(
            case_id=case.id,
            at=now,
            decision_type="diagnosis",
            action=diagnosis.klass.value,
            inputs={"reasoning": diagnosis.reasoning},
            rules_fired=[f"taxonomy:{diagnosis.taxonomy_version}"],
            confidence=diagnosis.confidence,
            outcome=diagnosis.authority,
        )
    )

    if diagnosis.authority == "hold-for-human":
        store.set_state(case.id, CaseState.HELD_FOR_HUMAN)
        store.record(
            AuditRecord(
                case_id=case.id,
                at=now,
                decision_type="stop",
                action="hold-for-human",
                inputs={"confidence": diagnosis.confidence},
                rules_fired=["CONFIDENCE_BELOW_THRESHOLD"],
                outcome="awaiting human review",
            )
        )
        return store.get_case(case.id) or case

    if diagnosis.klass not in RETRYABLE:
        store.record(
            AuditRecord(
                case_id=case.id,
                at=now,
                decision_type="escalation",
                action="retry-skipped",
                inputs={"class": diagnosis.klass.value},
                rules_fired=["SILENT_RETRY_CANNOT_SUCCEED"],
                outcome="needs an intervention a retry cannot provide",
            )
        )
        return store.get_case(case.id) or case

    # A balance failure is worth waiting a day for; a technical one is not.
    delay = (
        timedelta(days=1) if diagnosis.klass is DiagnosisClass.RECOVERABLE_BALANCE else timedelta()
    )
    decision = schedule_retry(now + delay, retry_count=case.retry_count, now=now)

    store.record(
        AuditRecord(
            case_id=case.id,
            at=now,
            decision_type="schedule",
            action=(
                f"retry at {decision.scheduled_for:%Y-%m-%d %H:%M %Z}"
                if decision.scheduled_for
                else "retry refused"
            ),
            inputs={
                "requested": decision.requested.isoformat() if decision.requested else None,
                "diagnosis": diagnosis.klass.value,
            },
            rules_fired=decision.rules_fired,
            rule_version=decision.rule_version,
            outcome=decision.reason,
        )
    )
    return store.get_case(case.id) or case
