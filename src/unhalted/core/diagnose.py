"""Diagnosis: the rules table first, the model only when it cannot resolve.

The split matters and is recorded on every diagnosis. A classification carrying
`source: rules-table` was reached deterministically and replays identically. One
carrying `source: model` did not, and is subject to the confidence banding in
`Diagnosis.authority`.

At C2 the model path is not implemented. An unmatched failure is classified
`unknown` and held for a human, which is what the specification requires anyway:
a novel failure is never guessed at, whatever a model might suggest.
"""

from __future__ import annotations

from unhalted.core import taxonomy
from unhalted.models import Diagnosis, DiagnosisSource, FailureSignal


def diagnose(signal: FailureSignal) -> Diagnosis:
    """Classify a failure from its Razorpay error fields."""
    rule, matched_key = taxonomy.lookup(signal.error_reason, signal.error_source, signal.error_step)

    if matched_key is None:
        reasoning = (
            f"no taxonomy entry for reason={signal.error_reason!r} "
            f"source={signal.error_source!r} step={signal.error_step!r}; "
            "held rather than guessed"
        )
    else:
        reasoning = f"matched {matched_key}: {rule.rationale}"

    return Diagnosis(
        klass=rule.klass,
        confidence=rule.confidence,
        source=DiagnosisSource.RULES_TABLE,
        reasoning=reasoning,
        taxonomy_version=taxonomy.TAXONOMY_VERSION,
    )
