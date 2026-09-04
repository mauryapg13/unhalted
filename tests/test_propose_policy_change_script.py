"""The report a human actually reads. The one property that matters most is
already tested in core/policy_change.py; this is about what gets printed.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from unhalted.core.policy_change import Proposal, ProposedChange

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "propose_policy_change.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("propose_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_failed_read_says_so_plainly(script) -> None:
    result = Proposal((), (), (), 0.0, failed=True, failure_reason="no model API key configured")
    out = script.render(result)
    assert "could not read this text" in out
    assert "no model API key configured" in out


def test_a_proposed_change_shows_current_and_proposed_and_the_quote(script) -> None:
    change = ProposedChange(
        field="retries.cap", current_value=3, proposed_value=4,
        quote="the retry cap is increased to four attempts",
        reasoning="the circular states a new cap",
    )
    result = Proposal((change,), (), (), 0.0004)
    out = script.render(result)
    assert "retries.cap" in out
    assert "current:   3" in out
    assert "proposed:  4" in out
    assert "the retry cap is increased to four attempts" in out
    assert "NOT APPLIED" in out


def test_dropped_and_unclear_both_appear(script) -> None:
    result = Proposal(
        (), ("something vague",), ("field.not.allowed: refused",), 0.0001,
    )
    out = script.render(result)
    assert "field.not.allowed: refused" in out
    assert "something vague" in out


def test_nothing_here_writes_to_the_policy_file(script) -> None:
    import inspect
    source = inspect.getsource(script)
    assert "write_text" not in source
    assert "yaml.safe_dump" not in source


def test_the_report_always_says_nothing_was_written(script) -> None:
    result = Proposal((), (), (), 0.0)
    out = script.render(result)
    assert "nothing has been written" in out
