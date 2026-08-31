"""Diagnosis: the rules table first, the model only when it cannot resolve.

The split is recorded on every diagnosis. A classification carrying
`source: rules-table` was reached deterministically and replays identically
against the same taxonomy version. One carrying `source: model` did not.

The model path is not implemented. An unmatched failure is classified `unknown`
and held for a human, which is what the specification requires regardless: a
novel failure is never guessed at, whatever a model might suggest.
"""

from __future__ import annotations

from unhalted.core import taxonomy
from unhalted.models import Diagnosis, DiagnosisSource, FailureSignal


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
