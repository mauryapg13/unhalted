"""The five documented scenarios classify to what the same rule table gives
anywhere else — this just guards the script's own numbers against the
taxonomy changing under it without anyone noticing.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import UTC, datetime

import pytest

from unhalted.core.diagnose import diagnose
from unhalted.models import FailureSignal

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "classify.py"
NOW = datetime(2026, 9, 4, tzinfo=UTC)


@pytest.fixture(scope="module")
def classify():
    spec = importlib.util.spec_from_file_location("classify_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_lists_five_scenarios(classify) -> None:
    assert len(classify.SCENARIOS) == 5


def test_every_scenario_actually_classifies_no_fabrication(classify) -> None:
    """Each reason must reach a real taxonomy rule, not fall through to the
    "no entry" branch — a scenario that stopped matching would silently turn
    into an unclassified case, which is the opposite of the point."""
    for reason, _gloss in classify.SCENARIOS:
        signal = FailureSignal(
            payment_id=f"pay_{reason}", customer_ref="c", amount_paise=100,
            occurred_at=NOW,
            source="test", method="card", error_reason=reason, error_source="gateway",
        )
        d = diagnose(signal)
        assert "no taxonomy entry" not in d.reasoning, f"{reason} no longer matches a rule"


def test_two_scenarios_reach_full_confidence_auto_execute(classify) -> None:
    """The two the write-up promised as the strongest contrast to what the
    captured fixtures classify as."""
    for reason in ("insufficient_fund", "gateway_technical_error"):
        signal = FailureSignal(
            payment_id=f"pay_{reason}", customer_ref="c", amount_paise=100,
            occurred_at=NOW,
            source="test", method="card", error_reason=reason, error_source="gateway",
        )
        d = diagnose(signal)
        assert d.confidence == 1.00
        assert d.authority == "auto-execute"
