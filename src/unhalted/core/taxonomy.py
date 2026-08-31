"""Failure classification from Razorpay's own error fields.

Razorpay reports three fields on a failed payment: `error_reason`, `error_source`
and `error_step`. Several reasons are ambiguous on their own — Razorpay's UPI
error documentation records that `payment_timed_out`, `gateway_technical_error`
and `credit_failed` each have more than one root cause, and those causes need
opposite recovery paths. Source and step usually disambiguate them, which is why
the table is keyed on all three rather than on the reason alone.

Scope note: this table is deliberately minimal. It covers the classes the
walking skeleton exercises. It is widened at C3, against the full documented
Razorpay card and UPI error lists.

On the confidence numbers
-------------------------
They are **provisional policy floors, not measured estimates**, and they are
written down here so nobody mistakes them for evidence.

A deterministic lookup does not have confidence the way a model does. If
`insufficient_funds` maps to `recoverable-balance`, that mapping is either right
or this table is wrong — it is not 97% likely. What each number actually encodes
is a decision about how much autonomy that mapping has earned, expressed on the
confidence scale so it passes through the same authority banding as a model's
output (see `Diagnosis.authority`).

Until they are measured, they say: this mapping is trusted enough to act on
without a human, or it is not.

At C8 each is replaced by the observed rate at which that mapping led to the
correct recovery path across the batch. At that point they become evidence. They
are not evidence now, and no number derived from them should be reported as if
they were.
"""

from __future__ import annotations

from typing import NamedTuple

from unhalted.models import DiagnosisClass

TAXONOMY_VERSION = "c2-minimal"

#: Matches any value for that position in the key.
ANY = "*"


class Rule(NamedTuple):
    klass: DiagnosisClass
    confidence: float
    rationale: str


#: (error_reason, error_source, error_step) -> Rule.
#: More specific keys are tried before wildcards.
#: Confidences here are provisional policy floors — see the module docstring.
TABLE: dict[tuple[str, str, str], Rule] = {
    ("insufficient_funds", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_BALANCE,
        0.97,
        "customer account lacked balance; the debit itself is well-formed",
    ),
    ("insufficient_fund", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_BALANCE,
        0.97,
        "card-flow spelling of insufficient balance",
    ),
    # A bank- or issuer-side failure at authorisation is not the customer's
    # doing. It is worth a silent retry and is not worth a message — nudging
    # someone about their bank's downtime is noise about a problem that is not
    # theirs.
    ("payment_failed", "bank", ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        0.90,
        "bank-side failure at authorisation; no customer action implied",
    ),
    ("payment_failed", "issuer", ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        0.90,
        "issuer-side failure at authorisation; no customer action implied",
    ),
    ("payment_declined", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_BALANCE,
        0.75,
        "funds could not be debited; balance is the most common cause",
    ),
}

#: Applied when nothing matches. Never guesses.
UNMATCHED = Rule(
    DiagnosisClass.UNKNOWN,
    0.0,
    "no taxonomy entry for this combination of reason, source and step",
)


def lookup(reason: str | None, source: str | None, step: str | None) -> tuple[Rule, str | None]:
    """Classify a failure, returning the rule and the key that matched.

    Tries the most specific key first, then progressively wildcards source and
    step. An unmatched failure is `unknown` with zero confidence — it is never
    guessed at, and the shell holds it for a human.
    """
    if not reason:
        return UNMATCHED, None

    for key in (
        (reason, source or ANY, step or ANY),
        (reason, source or ANY, ANY),
        (reason, ANY, ANY),
    ):
        if key in TABLE:
            return TABLE[key], "|".join(key)
    return UNMATCHED, None
