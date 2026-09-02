"""Diagnosis: the rules table first, and a way to say "not yet".

Three outcomes, not two. A failure is classified, or it is unrecognised and
held — or the signals conflict and nothing should be decided until the facts are
checked.

That third one is Feature 1's reconciliation loop, and it exists because
Razorpay documents a case where a failure is not a failure: a `payment.failed`
webhook can be followed by `payment.captured` for the same transaction when the
customer retries inside their UPI app and succeeds. Their words: *"This sequence
is expected behaviour."*

Retrying such a case debits somebody who has already paid. No rules table can
tell the difference, because the difference is not in the payload — it is in
what happened afterwards. So diagnosis returns a request to go and look.

The model does not do this. It is the seam the model will use: `diagnose()`
already has a return path for "I cannot decide from this alone", and the
verifying is the shell's job either way. What arrives later is a second reason
to take that path, not a new mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass

from unhalted.core import taxonomy
from unhalted.models import Diagnosis, DiagnosisSource, FailureSignal


@dataclass(frozen=True)
class VerificationNeeded:
    """The signals do not settle it. Check something before deciding."""

    reason: str
    check: str
    rule: str

    #: Kept so callers can treat both outcomes uniformly where it does not matter.
    klass = None


#: Reasons where the outcome at the switch is genuinely unknown from the payload
#: alone. Razorpay documents a late capture following exactly these.
UNSETTLED_REASONS = frozenset({
    "payment_timed_out",
    "gateway_technical_error",
    "payment_collect_request_expired",
})


def needs_verification(signal: FailureSignal) -> VerificationNeeded | None:
    """Whether this failure might not be one.

    Only asked when there is an order to check against — without one there is
    nothing to look at, and inventing a verification step that cannot run would
    be worse than not having one.
    """
    if not signal.order_id:
        return None
    if signal.error_reason not in UNSETTLED_REASONS:
        return None

    return VerificationNeeded(
        reason=(
            f"{signal.error_reason} leaves the outcome unknown from the payload alone; "
            "Razorpay documents a capture following such a failure when the customer "
            "retries in their own app"
        ),
        check=f"whether order {signal.order_id} has since been paid",
        rule="VERIFY_BEFORE_RETRY",
    )


def diagnose(signal: FailureSignal) -> Diagnosis:
    """Classify a failure from the four fields Razorpay gives us."""
    match = taxonomy.lookup(
        signal.method,
        signal.error_reason,
        signal.error_source,
        signal.error_step,
    )

    if not match.key:
        reasoning = (
            f"no taxonomy entry for method={signal.method!r} "
            f"reason={signal.error_reason!r} source={signal.error_source!r} "
            f"step={signal.error_step!r}; held rather than guessed"
        )
    else:
        reasoning = f"matched {match.key}. {match.reasoning}"

    return Diagnosis(
        klass=match.rule.klass,
        confidence=match.confidence,
        source=DiagnosisSource.RULES_TABLE,
        reasoning=reasoning,
        taxonomy_version=taxonomy.taxonomy_version(),
    )
