"""Whether confidence predicts outcome — measured, not modelled.

Issue #7 asked whether 0.90 and 0.70 are the right cut-points, and C8 could
not answer it: correctness on generated data is circular, because the
correct class is whatever the generator picked. A real terminal outcome is
not circular. `mark_recovered` is the first thing in this project that gives
a case a real, non-generated outcome — this groups every case that has
reached one by the confidence band its diagnosis fell into, and reports what
actually happened.

Small numbers are the honest state of things right now, not a bug in this
module. A rate computed from three cases is shown, and shown as what it is:
too few to conclude anything from, the same way a single reply parse would
be too few to trust a precision figure from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from unhalted.models import CaseState, Diagnosis

#: Below this many cases in a band, the rate shown is not a finding — it is
#: what happened, on a sample too small to generalise from. Not a measured
#: number; a common statistical rule of thumb for a proportion, stated as
#: policy rather than dressed up as more than that.
MIN_SAMPLE = 20

#: A case has a real outcome once it reaches one of these. OPEN and
#: HELD_FOR_HUMAN have not resolved yet and say nothing about calibration.
TERMINAL = frozenset({
    CaseState.RECOVERED, CaseState.UNRECOVERED,
    CaseState.CLOSED_REVOKED, CaseState.CLOSED_FALSE_FAILURE,
})

#: These close a case without "did the intervention recover the money" ever
#: being the live question — the customer revoked consent, or it was never
#: really a failure. Counting them against the recovery rate would blame the
#: diagnosis for an outcome the diagnosis was never trying to produce.
NOT_A_RECOVERY_QUESTION = frozenset({CaseState.CLOSED_REVOKED, CaseState.CLOSED_FALSE_FAILURE})

BANDS = ("auto-execute", "auto-execute-sampled-qa", "hold-for-human")


@dataclass
class BandOutcome:
    cases: int = 0
    recovered: int = 0

    @property
    def rate(self) -> float:
        return self.recovered / self.cases if self.cases else 0.0

    @property
    def reliable(self) -> bool:
        return self.cases >= MIN_SAMPLE


@dataclass
class Calibration:
    bands: dict[str, BandOutcome] = field(default_factory=lambda: {b: BandOutcome() for b in BANDS})
    excluded: int = 0
    still_open: int = 0

    @property
    def total_terminal(self) -> int:
        return sum(b.cases for b in self.bands.values()) + self.excluded


def measure(cases: list[tuple[Diagnosis | None, CaseState]]) -> Calibration:
    """`cases` is every (latest diagnosis, current state) this store holds —
    source-agnostic the same way `outcomes.classify` is, so a real store and
    a test fixture reduce to the same shape."""
    result = Calibration()
    for diagnosis, state in cases:
        if diagnosis is None:
            continue
        if state not in TERMINAL:
            result.still_open += 1
            continue
        if state in NOT_A_RECOVERY_QUESTION:
            result.excluded += 1
            continue
        band = result.bands[diagnosis.authority]
        band.cases += 1
        if state is CaseState.RECOVERED:
            band.recovered += 1
    return result


def render(c: Calibration) -> str:
    lines = [
        "",
        "CALIBRATION — measured on real terminal outcomes, never generated ones",
        "",
        f"  {'authority':<26}{'cases':>7}{'recovered':>11}{'rate':>8}",
        "  " + "-" * 52,
    ]
    for name in BANDS:
        b = c.bands[name]
        flag = "" if b.reliable or b.cases == 0 else "  (too few to conclude anything)"
        lines.append(f"  {name:<26}{b.cases:>7}{b.recovered:>11}{b.rate:>7.0%}{flag}")

    lines += [
        "",
        f"  still open or held, no outcome yet: {c.still_open}",
        f"  excluded (revoked or false-failure; not a recovery question): {c.excluded}",
        "",
    ]

    if c.total_terminal == 0:
        lines.append(
            "  no case in this store has reached a real terminal outcome yet — nothing to"
        )
        lines.append(
            "  measure. This is the honest state before real recovered/unrecovered volume exists."
        )
    elif all(not c.bands[b].reliable for b in BANDS):
        lines.append(
            f"  every band is below {MIN_SAMPLE} cases. The rates above are what happened, not a"
        )
        lines.append(
            "  claim that the confidence thresholds are calibrated — that needs real volume this"
        )
        lines.append("  account does not have yet.")
    lines.append("")
    return "\n".join(lines)
