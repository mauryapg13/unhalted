"""Retry scheduling. The shell decides when, and refuses when it must.

A recommendation may come from anywhere — a rules table, a model, a customer's
stated payday. It is a request, never an instruction. This module is the only
thing that turns a request into a scheduled debit, and it refuses any time NPCI
forbids regardless of how confident the requester was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from unhalted.models import DiagnosisClass
from unhalted.shell import windows

#: No more attempts than this per billing cycle, matching NPCI's allowance of
#: one execution plus three retries. Not overridable at any confidence.
RETRY_CAP = 3

#: How long to wait before each attempt, by what went wrong.
#:
#: A technical failure is transient by definition — a bank was down, a gateway
#: timed out. Retrying in the same second retries into the same outage, fails
#: again, and spends one of the three attempts NPCI allows. Three of those can
#: exhaust a whole cycle's allowance inside a downtime lasting minutes.
#:
#: A balance failure needs money to arrive, which takes a day at minimum and
#: usually a payday. A notification gap needs the 25 hours Razorpay requires
#: between a pre-debit alert and the charge it precedes.
BACKOFF: dict[DiagnosisClass, tuple[timedelta, ...]] = {
    DiagnosisClass.RECOVERABLE_TECHNICAL: (
        timedelta(minutes=30),
        timedelta(hours=2),
        timedelta(hours=6),
    ),
    DiagnosisClass.RECOVERABLE_BALANCE: (
        timedelta(days=1),
        timedelta(days=1),
        timedelta(days=2),
    ),
    DiagnosisClass.NOTIFICATION_GAP: (
        timedelta(hours=25),
        timedelta(hours=25),
        timedelta(hours=25),
    ),
}

#: Used when a class has no schedule of its own.
DEFAULT_BACKOFF = timedelta(hours=6)


def backoff_for(klass: DiagnosisClass | None, attempt: int) -> timedelta:
    """How long to wait before attempt `attempt` (zero-indexed) of this class.

    Kept beside the window rules because it is timing policy, and kept out of
    `schedule_retry` because it applies to automatic retries only — a retry
    realigned to a customer's stated payday must land on that day.
    """
    schedule = BACKOFF.get(klass) if klass else None
    if not schedule:
        return DEFAULT_BACKOFF
    return schedule[min(attempt, len(schedule) - 1)]


@dataclass(frozen=True)
class ScheduleDecision:
    """The outcome of asking the shell to schedule a retry."""

    scheduled_for: datetime | None
    accepted: bool
    reason: str
    rule_version: str
    rules_fired: list[str]
    requested: datetime | None = None

    @property
    def was_moved(self) -> bool:
        return self.accepted and self.requested is not None and self.scheduled_for != self.requested


def schedule_retry(
    requested_at: datetime,
    retry_count: int,
    *,
    now: datetime | None = None,
    method: str | None = None,
) -> ScheduleDecision:
    """Schedule a retry at or after `requested_at`, or refuse.

    This honours the time it is given. Backoff is *not* applied here, because
    not every retry wants it: a retry realigned to a date the customer named
    should land on that date, not six hours after it. Callers scheduling an
    automatic retry add `backoff_for()` themselves; callers honouring a promise
    do not.

    A request landing inside an NPCI restricted band is not rejected outright —
    it is moved to the end of that band and logged as a violation, so the
    recommendation is honoured as closely as regulation permits and the fact
    that it had to be corrected is recorded.
    """
    rules: list[str] = []

    if retry_count >= RETRY_CAP:
        return ScheduleDecision(
            scheduled_for=None,
            accepted=False,
            reason=f"retry cap of {RETRY_CAP} reached for this billing cycle",
            rule_version=windows.WINDOW_RULE_VERSION,
            rules_fired=["STOP_RULE:RETRY_CAP"],
            requested=requested_at,
        )

    requested = windows.as_ist(requested_at)
    floor = windows.as_ist(now) if now else requested
    candidate = max(requested, floor)
    if candidate != requested:
        rules.append("NOT_IN_THE_PAST")


    # Scoped to the rail. NPCI's bands govern UPI Autopay, so a card retry is
    # not moved and no violation is recorded against it — the audit trail should
    # not say a rule fired when the rule did not apply.
    check = windows.is_execution_allowed(candidate, method=method)
    if not check.allowed:
        rules.append(f"{check.code}:{check.reason}")
        candidate = windows.next_allowed_execution(candidate, method=method)

    return ScheduleDecision(
        scheduled_for=candidate,
        accepted=True,
        reason=check.reason if check.allowed else f"moved out of restricted band: {check.reason}",
        rule_version=check.rule_version,
        rules_fired=rules,
        requested=requested,
    )
