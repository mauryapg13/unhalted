"""Retry scheduling. The shell decides when, and refuses when it must.

A recommendation may come from anywhere — a rules table, a model, a customer's
stated payday. It is a request, never an instruction. This module is the only
thing that turns a request into a scheduled debit, and it refuses any time NPCI
forbids regardless of how confident the requester was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from unhalted.shell import windows

#: No more attempts than this per billing cycle, matching NPCI's allowance of
#: one execution plus three retries. Not overridable at any confidence.
RETRY_CAP = 3


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
) -> ScheduleDecision:
    """Schedule a retry at or after `requested_at`, or refuse.

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

    check = windows.is_execution_allowed(candidate)
    if not check.allowed:
        rules.append(f"{check.code}:{check.reason}")
        candidate = windows.next_allowed_execution(candidate)

    return ScheduleDecision(
        scheduled_for=candidate,
        accepted=True,
        reason=check.reason if check.allowed else f"moved out of restricted band: {check.reason}",
        rule_version=check.rule_version,
        rules_fired=rules,
        requested=requested,
    )
