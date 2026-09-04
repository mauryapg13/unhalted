"""Whether confidence predicts outcome — and the honesty that a handful of
real cases is not enough to conclude anything, which it currently is not.
"""

from __future__ import annotations

import pytest

from unhalted.measure.calibration import MIN_SAMPLE, measure, render
from unhalted.models import CaseState, Diagnosis, DiagnosisClass, DiagnosisSource


def diagnosis(confidence: float) -> Diagnosis:
    return Diagnosis(
        klass=DiagnosisClass.RECOVERABLE_TECHNICAL, confidence=confidence,
        source=DiagnosisSource.RULES_TABLE, reasoning="x", taxonomy_version="t",
    )


def test_an_empty_store_measures_nothing() -> None:
    c = measure([])
    assert c.total_terminal == 0
    assert all(b.cases == 0 for b in c.bands.values())


def test_open_and_held_cases_do_not_count_as_terminal() -> None:
    c = measure([
        (diagnosis(0.95), CaseState.OPEN),
        (diagnosis(0.95), CaseState.HELD_FOR_HUMAN),
    ])
    assert c.still_open == 2
    assert c.total_terminal == 0


def test_a_recovered_case_counts_in_its_confidence_band() -> None:
    c = measure([(diagnosis(0.95), CaseState.RECOVERED)])
    assert c.bands["auto-execute"].cases == 1
    assert c.bands["auto-execute"].recovered == 1
    assert c.bands["auto-execute"].rate == 1.0


def test_an_unrecovered_case_counts_but_not_as_a_recovery() -> None:
    c = measure([(diagnosis(0.95), CaseState.UNRECOVERED)])
    assert c.bands["auto-execute"].cases == 1
    assert c.bands["auto-execute"].recovered == 0


def test_revoked_and_false_failure_are_excluded_not_counted_as_failures() -> None:
    """Blaming the diagnosis for a customer revoking consent, or a payment
    that was never really a failure, would misattribute the outcome."""
    c = measure([
        (diagnosis(0.95), CaseState.CLOSED_REVOKED),
        (diagnosis(0.95), CaseState.CLOSED_FALSE_FAILURE),
    ])
    assert c.excluded == 2
    assert all(b.cases == 0 for b in c.bands.values())


def test_a_case_with_no_diagnosis_is_skipped_not_crashed_on() -> None:
    c = measure([(None, CaseState.RECOVERED)])
    assert c.total_terminal == 0


def test_bands_split_correctly_by_authority() -> None:
    c = measure([
        (diagnosis(0.95), CaseState.RECOVERED),      # auto-execute
        (diagnosis(0.75), CaseState.UNRECOVERED),    # auto-execute-sampled-qa
        (diagnosis(0.50), CaseState.RECOVERED),      # hold-for-human
    ])
    assert c.bands["auto-execute"].cases == 1
    assert c.bands["auto-execute-sampled-qa"].cases == 1
    assert c.bands["hold-for-human"].cases == 1


def test_a_band_below_min_sample_is_flagged_as_unreliable() -> None:
    c = measure([(diagnosis(0.95), CaseState.RECOVERED)])
    assert not c.bands["auto-execute"].reliable
    assert c.bands["auto-execute"].cases < MIN_SAMPLE


def test_the_report_never_hides_that_the_sample_is_too_small() -> None:
    c = measure([(diagnosis(0.95), CaseState.RECOVERED)])
    out = render(c)
    assert "too few to conclude" in out.lower() or "too few" in out.lower()


def test_the_report_says_so_when_there_is_nothing_to_measure_yet() -> None:
    out = render(measure([]))
    assert "nothing to" in out.lower() or "no case" in out.lower()


def test_the_report_never_claims_calibration_from_a_small_sample() -> None:
    """The whole reason this exists rather than a single confident-sounding
    percentage: a handful of real cases must not read as a settled finding."""
    c = measure([(diagnosis(0.95), CaseState.RECOVERED)] * 3)
    out = render(c)
    assert "not a" in out
    assert "confidence thresholds are calibrated" in out


@pytest.mark.parametrize("state", [CaseState.OPEN, CaseState.HELD_FOR_HUMAN])
def test_every_non_terminal_state_is_excluded(state) -> None:
    c = measure([(diagnosis(0.95), state)])
    assert c.total_terminal == 0
    assert c.still_open == 1
