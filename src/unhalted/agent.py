"""The control loop.

The agent is this loop, not the model. It holds case state, chooses the next
action from a bounded set, and records every decision. At C2 the loop is narrow:
open a case, diagnose it, schedule a retry if the diagnosis warrants one. Each
later checkpoint widens the action set without changing the shape.
"""

from __future__ import annotations

from datetime import datetime

from unhalted.core.diagnose import diagnose, needs_verification
from unhalted.core.reply import parse as parse_reply
from unhalted.models import (
    AuditRecord,
    Case,
    CaseState,
    DiagnosisClass,
    FailureSignal,
    ParsedReply,
)
from unhalted.shell import ladder, limits, replies, stops, verify, windows
from unhalted.shell.scheduler import backoff_for, schedule_retry
from unhalted.store import Store

#: Classes a silent retry can plausibly fix. Anything else needs a different rung.
RETRYABLE = {
    DiagnosisClass.RECOVERABLE_TECHNICAL,
    DiagnosisClass.RECOVERABLE_BALANCE,
}


def handle_failure(
    store: Store,
    signal: FailureSignal,
    *,
    verifier: verify.Verifier | None = None,
    now: datetime | None = None,
) -> Case:
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

    # Before classifying, ask whether this is even a failure. Razorpay documents
    # a capture following a failure on the same transaction, and retrying such a
    # case debits somebody who has already paid — a worse outcome than never
    # recovering.
    pending_check = needs_verification(signal)
    if pending_check is not None:
        store.record(
            AuditRecord(
                case_id=case.id,
                at=now,
                decision_type="verification",
                action="check before deciding",
                inputs={"check": pending_check.check, "why": pending_check.reason},
                rules_fired=[pending_check.rule],
                outcome="no retry is scheduled until this resolves",
            )
        )
        settled = _verify(store, case, signal, pending_check, verifier, now)
        if settled:
            return store.get_case(case.id) or case

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

    # The diagnosis decides where this case joins the ladder, rather than
    # everything starting at the bottom. A dead mandate does not begin with
    # silent retries: they would spend NPCI's allowance proving what is known.
    rung = ladder.entry_rung(diagnosis.klass)
    if rung is None:
        store.record(
            AuditRecord(
                case_id=case.id, at=now, decision_type="escalation",
                action="no intervention applies",
                inputs={"class": diagnosis.klass.value},
                rules_fired=["NO_LADDER"],
                outcome="nothing on the ladder can recover this",
            )
        )
        return store.get_case(case.id) or case

    economics = ladder.evaluate(rung, case.amount_paise)
    if not economics.approved:
        store.set_state(case.id, CaseState.UNRECOVERED)
        store.cancel_pending("UNECONOMIC", case_id=case.id)
        store.record(
            AuditRecord(
                case_id=case.id, at=now, decision_type="escalation",
                action=f"ladder terminated at rung {rung.value} as uneconomic",
                inputs={
                    "rung": ladder.LADDER[rung].name,
                    "calculation": economics.calculation,
                    "rested_on_an_assumed_rate": economics.assumption_used,
                },
                rules_fired=economics.rules_fired,
                rule_version=economics.rule_version,
                outcome=economics.reason,
            )
        )
        return store.get_case(case.id) or case

    store.record(
        AuditRecord(
            case_id=case.id, at=now, decision_type="escalation",
            action=f"entering at rung {rung.value}: {ladder.LADDER[rung].name}",
            inputs={
                "class": diagnosis.klass.value,
                "cost_paise": ladder.LADDER[rung].cost_paise,
                "calculation": economics.calculation,
                "rested_on_an_assumed_rate": economics.assumption_used,
            },
            rules_fired=economics.rules_fired,
            rule_version=economics.rule_version,
            outcome=ladder.LADDER[rung].why,
        )
    )

    if rung is not ladder.Rung.SILENT_RETRY:
        store.schedule_action(case.id, case.customer_ref, ladder.LADDER[rung].name, None, now)
        return store.get_case(case.id) or case

    # Before any retry is scheduled, the money has to be permissible. This is
    # not the same question as whether the retry is well-timed, and it is asked
    # first: a debit above the mandate's ceiling should never be scheduled at
    # all, however good the window is.
    limit = limits.check(
        signal.amount_paise,
        signal.method,
        mandate_max_paise=signal.mandate_max_paise,
    )
    if not limit.may_attempt:
        store.record(
            AuditRecord(
                case_id=case.id,
                at=now,
                decision_type="schedule",
                action="retry refused on amount",
                inputs={
                    "amount_paise": signal.amount_paise,
                    "method": signal.method,
                    "mandate_max_paise": signal.mandate_max_paise,
                    "outcome": limit.outcome.value,
                },
                rules_fired=[limit.code] if limit.code else [],
                rule_version=limit.rule_version,
                outcome=limit.reason,
            )
        )
        return store.get_case(case.id) or case

    # Backoff is the scheduler's business, not the loop's — it is a timing
    # policy and belongs beside the window rules that constrain it.
    wait = backoff_for(diagnosis.klass, case.retry_count)
    decision = schedule_retry(now + wait, retry_count=case.retry_count, now=now)

    # Recorded as pending work, not only as a line in the audit trail. A
    # scheduled retry that nothing tracks is one a stop rule cannot cancel: a
    # revocation would leave it armed and the customer would be charged after
    # withdrawing permission. The audit says what was decided; the pending table
    # is what makes it cancellable.
    if decision.scheduled_for:
        store.schedule_action(
            case.id, case.customer_ref, "retry", decision.scheduled_for, now
        )

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


def apply_stop(
    store: Store,
    code: str,
    *,
    case_id: str,
    customer_ref: str,
    detail: str = "",
    now: datetime | None = None,
) -> int:
    """Fire a stop rule. Returns how many pending actions it cancelled.

    Cancellation happens in one transaction, so the specification's requirement
    holds literally: a revocation arriving while a retry, two nudges and a voice
    callback are pending cancels all four together, and no partial execution is
    possible.

    Scope decides reach. A chargeback freezes everything for that customer, not
    only the case the notice arrived on — continuing to chase someone who has
    formally disputed a charge is how a complaint becomes a regulatory problem.
    """
    rule = stops.rule(code)
    now = windows.as_ist(now or datetime.now(tz=windows.IST))

    if rule.scope is stops.StopScope.CASE:
        cancelled = store.cancel_pending(rule.code, case_id=case_id)
    else:
        # Customer and merchant scope both reach past this one case. Merchant
        # scope is not implemented as a merchant sweep — there is one merchant —
        # so it behaves as customer scope and says so rather than pretending.
        cancelled = store.cancel_pending(rule.code, customer_ref=customer_ref)

    if rule.terminal_state is not None:
        store.set_state(case_id, rule.terminal_state)

    store.record(
        AuditRecord(
            case_id=case_id,
            at=now,
            decision_type="stop",
            action=rule.action,
            inputs={
                "trigger": rule.trigger,
                "scope": rule.scope.value,
                "detail": detail,
                "sla_seconds": rule.sla.total_seconds() if rule.sla else None,
            },
            rules_fired=[stops.audit_code(code)],
            outcome=(
                f"cancelled {cancelled} pending action(s); {rule.why}"
            ),
        )
    )
    return cancelled


def handle_reply(
    store: Store,
    case: Case,
    text: str,
    *,
    context: str = "",
    now: datetime | None = None,
) -> tuple[ParsedReply, replies.ReplyOutcome]:
    """Take a customer reply from words to consequence, and record all of it.

    This lives here rather than in whatever surface received the message,
    because the consequence of a reply is the agent's business and needs to be
    the same whether the words arrived by WhatsApp, by console, or by replay.
    """
    now = windows.as_ist(now or datetime.now(tz=windows.IST))
    parsed = parse_reply(text, context=context)
    outcome = replies.decide(parsed, today=now.date())

    store.record(
        AuditRecord(
            case_id=case.id,
            at=now,
            decision_type="reply",
            action="parsed" if not parsed.failed else "parse failed",
            inputs={
                "reply": text,
                "intents": [
                    {"type": i.type.value, "confidence": i.confidence, "evidence": i.evidence}
                    for i in parsed.intents
                ],
                "sentiment": parsed.sentiment.value,
                "date_proposed": parsed.payment_date_raw,
                "attempts": parsed.attempts,
            },
            rules_fired=outcome.rules_fired,
            rule_version=outcome.rule_version,
            model_name=parsed.model_name,
            confidence=max((i.confidence for i in parsed.intents), default=None),
            outcome="; ".join(outcome.reasons),
        )
    )

    if outcome.stop_code:
        apply_stop(
            store, outcome.stop_code, case_id=case.id,
            customer_ref=case.customer_ref, detail=text, now=now,
        )
        return parsed, outcome

    # Nothing automated may remain scheduled on a case a person now owns.
    # A customer who has asked to cancel must not be charged while waiting for
    # someone to action it, and a reply nobody could read must not silently
    # leave a retry armed.
    if outcome.needs_human:
        cancelled = store.cancel_pending("HELD_FOR_HUMAN", case_id=case.id)
        store.set_state(case.id, CaseState.HELD_FOR_HUMAN)
        store.record(
            AuditRecord(
                case_id=case.id,
                at=now,
                decision_type="stop",
                action="held for a human",
                inputs={"reply": text},
                rules_fired=["HELD_FOR_HUMAN"],
                outcome=(
                    f"cancelled {cancelled} pending action(s); nothing automated runs on a "
                    "case awaiting a person"
                ),
            )
        )
        return parsed, outcome

    if outcome.realign_to:
        store.cancel_pending("REALIGNED", case_id=case.id)
        target = datetime.combine(outcome.realign_to, now.timetz())
        decision = schedule_retry(target, retry_count=case.retry_count, now=now)
        if decision.scheduled_for:
            store.schedule_action(
                case.id, case.customer_ref, "retry", decision.scheduled_for, now
            )
        store.record(
            AuditRecord(
                case_id=case.id,
                at=now,
                decision_type="schedule",
                action=(
                    f"retry realigned to {decision.scheduled_for:%Y-%m-%d %H:%M %Z}"
                    if decision.scheduled_for else "realignment refused"
                ),
                inputs={"promised": outcome.realign_to.isoformat()},
                rules_fired=decision.rules_fired,
                rule_version=decision.rule_version,
                outcome=decision.reason,
            )
        )

    return parsed, outcome


def _verify(
    store: Store,
    case: Case,
    signal: FailureSignal,
    request,
    verifier: verify.Verifier | None,
    now: datetime,
) -> bool:
    """Perform a verification. Returns True when the case is settled by it.

    A verification that cannot be performed holds the case. Treating "could not
    check" as "not paid" is precisely the assumption that double-debits
    somebody, so it is never made.
    """
    if verifier is None:
        store.set_state(case.id, CaseState.HELD_FOR_HUMAN)
        store.record(
            AuditRecord(
                case_id=case.id, at=now, decision_type="stop", action="held for a human",
                inputs={"check": request.check},
                rules_fired=["VERIFICATION_UNAVAILABLE"],
                outcome=(
                    "no verifier is configured, so it is unknown whether this order was "
                    "already paid; assuming it was not is how a customer gets charged twice"
                ),
            )
        )
        return True

    try:
        result = verifier.order_settled(signal.order_id or "")
    except verify.VerificationUnavailable as e:
        store.set_state(case.id, CaseState.HELD_FOR_HUMAN)
        store.record(
            AuditRecord(
                case_id=case.id, at=now, decision_type="stop", action="held for a human",
                inputs={"check": request.check, "error": str(e)},
                rules_fired=["VERIFICATION_UNAVAILABLE"],
                outcome="the check could not be performed; not-checked is not the same as not-paid",
            )
        )
        return True

    if result.already_paid:
        store.set_state(case.id, CaseState.CLOSED_FALSE_FAILURE)
        store.cancel_pending("FALSE_FAILURE", case_id=case.id)
        store.record(
            AuditRecord(
                case_id=case.id, at=now, decision_type="stop", action="closed as a false failure",
                inputs={"checked": result.checked},
                rules_fired=["FALSE_FAILURE"],
                outcome=(
                    f"{result.detail}. No retry ever fires, and this is not counted as a "
                    "recovery — the money was never lost"
                ),
            )
        )
        return True

    store.record(
        AuditRecord(
            case_id=case.id, at=now, decision_type="verification", action="confirmed unpaid",
            inputs={"checked": result.checked},
            rules_fired=["VERIFIED_UNPAID"],
            outcome=f"{result.detail}; recovery may proceed",
        )
    )
    return False
