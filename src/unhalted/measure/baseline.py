"""Razorpay's own retry behaviour, as the control group.

Not a strawman we invented. From their subscription documentation:

    Let T=0 be the charge day. On this day, we attempt the charge. If the charge
    fails, the subscription moves to the `pending` state, and we automatically
    reattempt the charge on T+1 day. If the charge fails again, we automatically
    reattempt the charge two more times on T+2 and T+3 days, respectively. If
    the charge still fails, the subscription moves to the `halted` state.

Three retries, one a day, whatever went wrong. No diagnosis, no message to the
customer, no memory of anything they said, and no awareness of NPCI's execution
windows — which is what makes it a fair and citable baseline rather than one
chosen to lose.

Everything this simulates is what the baseline *does*, never what it achieves.
Whether a retry succeeds is not modelled here, because modelling it would decide
the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from unhalted.models import DiagnosisClass, FailureSignal
from unhalted.shell import windows
from unhalted.shell.ladder import ENTRY, Rung

#: Razorpay documents three automatic retries on consecutive days.
BASELINE_RETRIES = 3

#: Classes where a retry provably cannot work, from Razorpay's own error
#: descriptions. An expired card does not un-expire overnight.
FUTILE = {DiagnosisClass.MANDATE_STATE_BROKEN, DiagnosisClass.CUSTOMER_INTENT_REVOKED}


@dataclass
class BaselineRun:
    """What the documented policy did with one failure."""

    attempts: int = 0
    attempts_in_restricted_window: int = 0
    futile_attempts: int = 0
    messages_sent: int = 0
    intervention_paise: int = 0
    scheduled_at: list[str] = field(default_factory=list)


def run(signal: FailureSignal, klass: DiagnosisClass) -> BaselineRun:
    """Replay the documented behaviour against one failure.

    `klass` is supplied only to count how many of the attempts were spent on a
    failure a retry cannot fix. The baseline itself never sees it — that is the
    whole point of it.
    """
    result = BaselineRun()
    at = windows.as_ist(signal.occurred_at)

    for _ in range(BASELINE_RETRIES):
        at = at + timedelta(days=1)
        result.attempts += 1
        result.scheduled_at.append(at.isoformat())
        if not windows.is_execution_allowed(at).allowed:
            result.attempts_in_restricted_window += 1
        if klass in FUTILE:
            result.futile_attempts += 1

    return result


def agent_would_enter_at(klass: DiagnosisClass) -> Rung | None:
    """Where the agent starts, for comparison against three blind retries."""
    return ENTRY.get(klass)
