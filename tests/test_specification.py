"""The Gherkin specification must always parse.

The feature files under tests/features are the contract this project is built
against. If one stops parsing, or loses its scenarios, everything downstream is
built on sand — so that failure surfaces here rather than as a confusing
collection error somewhere else.
"""

from __future__ import annotations

import pathlib

import pytest
from pytest_bdd.parser import FeatureParser

FEATURES_DIR = pathlib.Path(__file__).parent / "features"
FEATURE_FILES = sorted(FEATURES_DIR.glob("*.feature"))

EXPECTED = {
    "audit_measurement",
    "diagnosis",
    "escalation_ladder",
    "human_gates",
    "reply_understanding",
    "retry_orchestration",
    "stopping_rules",
}


def test_every_expected_feature_file_is_present() -> None:
    assert {f.stem for f in FEATURE_FILES} == EXPECTED


@pytest.mark.parametrize("path", FEATURE_FILES, ids=lambda p: p.stem)
def test_feature_file_parses_and_has_scenarios(path: pathlib.Path) -> None:
    feature = FeatureParser(str(FEATURES_DIR), path.name).parse()
    assert feature.name, f"{path.name} has no Feature: line"
    assert feature.scenarios, f"{path.name} declares no scenarios"


def test_npci_restricted_bands_are_both_specified() -> None:
    """Guards the correction made to the spec.

    The 17:00-21:30 band was missing from the original specification, which
    would have permitted an evening retry that NPCI forbids. If it ever
    disappears again, this fails.
    """
    text = (FEATURES_DIR / "retry_orchestration.feature").read_text()
    assert "10:00-13:00" in text
    assert "17:00-21:30" in text
