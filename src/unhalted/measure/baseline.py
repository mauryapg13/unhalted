"""Razorpay's own retry behaviour, as the control group.

Not a strawman we invented. It is what their subscription documentation says
happens today, and it differs by payment method — which an earlier version of
this module got wrong by applying the UPI model to all three.

Source: `payments/subscriptions/payment-retries.md`, "Retry Model", which
presents the three methods as separate tabs. Verified 2026-09-03.

UPI, documented in full
-----------------------
    Let T=0 be the charge day. On this day, we attempt the charge. If the charge
    fails, the subscription moves to the `pending` state, and we automatically
    reattempt the charge on T+1 day. If the charge fails again, we automatically
    reattempt the charge two more times on T+2 and T+3 days, respectively. If
    the charge still fails, the subscription moves to the `halted` state.

Cards, documented in full
-------------------------
    In a T+3 days cycle, we will retry the payment thrice. That is, once every
    day for 3 days, excluding the date of the charge.

Emandate, documented only in part
---------------------------------
    In failure scenarios, we attempt to retry only when we get the confirmation
    or rejection of the last payment, as it may take more than 24 hours.

They state no retry *count* for emandate, and explicitly no daily cadence: the
next attempt waits on the previous one settling. They also document bank-holiday
shifting (T falls on a holiday, charge T-1; T and T-1 both holidays, charge T-3)
which this does not model, because a holiday calendar is not something the
project has.

So the emandate count is an assumption, carried on the result as one. The
interval is modelled at the fastest their wording permits — exactly 24 hours —
which is the reading most favourable to the baseline, and therefore the one that
cannot inflate the agent's advantage.

On the NPCI execution bands
---------------------------
The restricted bands are a rule about **UPI Autopay** execution. Cards do not
route through that rail and emandate settles through NACH, so counting a card or
emandate retry as a band violation credited this system with an advantage it
does not have. Violations are now counted only where the rule applies. On a
mandate-heavy mix that took the figure from 705 to 213.

Everything here simulates what the baseline *does*, never what it achieves.
Whether a retry succeeds is not modelled, because modelling it would decide the
comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from unhalted.models import DiagnosisClass, FailureSignal
from unhalted.shell import windows

#: Razorpay documents three automatic retries for UPI and for cards.
BASELINE_RETRIES = 3

#: Classes where a retry provably cannot work, from Razorpay's own error
#: descriptions. An expired card does not un-expire overnight.
FUTILE = {DiagnosisClass.MANDATE_STATE_BROKEN, DiagnosisClass.CUSTOMER_INTENT_REVOKED}


@dataclass(frozen=True)
class RetryModel:
    """How the documented policy behaves for one payment method."""

    retries: int
    interval: timedelta
    #: Whether NPCI's UPI Autopay execution bands govern this rail at all.
    subject_to_npci_bands: bool
    #: False where Razorpay states no count and we have supplied one.
    count_is_documented: bool
    note: str


UPI = RetryModel(
    retries=BASELINE_RETRIES,
    interval=timedelta(days=1),
    subject_to_npci_bands=True,
    count_is_documented=True,
    note="T+1, T+2, T+3 then halted; the only method NPCI's autopay bands govern",
)

CARD = RetryModel(
    retries=BASELINE_RETRIES,
    interval=timedelta(days=1),
    subject_to_npci_bands=False,
    count_is_documented=True,
    note="thrice, once a day, excluding the charge date; not routed through UPI Autopay",
)

EMANDATE = RetryModel(
    retries=BASELINE_RETRIES,
    interval=timedelta(days=1),
    subject_to_npci_bands=False,
    count_is_documented=False,
    note=(
        "Razorpay states no retry count for emandate and says the next attempt waits "
        "on the previous one settling, which may exceed 24 hours. Three attempts at "
        "exactly 24 hours is an assumption, and is the fastest their wording allows"
    ),
)

#: Emandate is offered over netbanking, debit card and Aadhaar, and NACH shares
#: its settlement behaviour.
MODELS: dict[str, RetryModel] = {
    "upi": UPI,
    "card": CARD,
    "emandate": EMANDATE,
    "netbanking": EMANDATE,
    "nach": EMANDATE,
}

#: A signal that does not say how it was paid. The general Subscriptions flow
#: says a failed payment is retried "the following day" until halted, so the
#: shape holds; the method-specific facts do not, and NPCI's UPI rule is not
#: assumed to apply to a rail we cannot name.
UNKNOWN_METHOD = RetryModel(
    retries=BASELINE_RETRIES,
    interval=timedelta(days=1),
    subject_to_npci_bands=False,
    count_is_documented=False,
    note="no method on the signal; the general subscription flow is assumed",
)


def model_for(method: str | None) -> RetryModel:
    return MODELS.get((method or "").lower(), UNKNOWN_METHOD)


@dataclass
class BaselineRun:
    """What the documented policy did with one failure."""

    attempts: int = 0
    attempts_in_restricted_window: int = 0
    futile_attempts: int = 0
    messages_sent: int = 0
    intervention_paise: int = 0
    scheduled_at: list[str] = field(default_factory=list)
    #: True when any part of this replay rests on something Razorpay does not
    #: document. Reported rather than buried, the same way the ladder reports
    #: a decision that used an unmeasured success rate.
    assumption_used: bool = False
    notes: list[str] = field(default_factory=list)


def run(signal: FailureSignal, klass: DiagnosisClass) -> BaselineRun:
    """Replay the documented behaviour against one failure.

    `klass` is supplied only to count how many of the attempts were spent on a
    failure a retry cannot fix. The baseline itself never sees it — that is the
    whole point of it.
    """
    policy = model_for(signal.method)
    result = BaselineRun(
        assumption_used=not policy.count_is_documented,
        notes=[policy.note],
    )
    at = windows.as_ist(signal.occurred_at)

    for _ in range(policy.retries):
        at = at + policy.interval
        result.attempts += 1
        result.scheduled_at.append(at.isoformat())
        if policy.subject_to_npci_bands and not windows.is_execution_allowed(at).allowed:
            result.attempts_in_restricted_window += 1
        if klass in FUTILE:
            result.futile_attempts += 1

    return result

