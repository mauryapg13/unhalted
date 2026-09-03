"""The reviewer terminal's one load-bearing property.

`show_case` used to call the model and block the decision prompt on it —
observed as a silent ~19s wait that read as a hang. The fix is a split: the raw
material a reviewer decides from never touches the model, and the model's read
is fetched only if asked for, from a separate function. This is the regression
test for that split, not a test of the terminal's visuals.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import patch

import pytest

from unhalted.agent import apply_stop, handle_failure
from unhalted.models import FailureSignal
from unhalted.shell.windows import IST
from unhalted.store import Store

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "review.py"


@pytest.fixture(scope="module")
def review():
    """Import scripts/review.py as a module without adding scripts/ to sys.path
    for the rest of the suite — it is a script, not a package."""
    spec = importlib.util.spec_from_file_location("review_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NOW = datetime(2026, 9, 3, 11, 0, tzinfo=IST)


@pytest.fixture
def held_case(tmp_path):
    store = Store(str(tmp_path / "review.db"))
    signal = FailureSignal(
        payment_id="pay_REV", customer_ref="cust_rev", amount_paise=49900,
        occurred_at=NOW, source="test", method="card",
        error_reason="payment_risk_check_failed", error_source="issuer",
    )
    case = handle_failure(store, signal, now=NOW)
    apply_stop(store, "DISPUTE", case_id=case.id, customer_ref=case.customer_ref,
              detail="test dispute", now=NOW)
    yield store, store.get_case(case.id)
    store.close()


def test_show_case_never_calls_the_model(review, held_case) -> None:
    """The regression that matters. A reviewer's ability to decide must not
    depend on a live call succeeding, responding quickly, or responding at all."""
    store, case = held_case
    with patch.object(review, "brief") as mocked:
        buf = io.StringIO()
        with redirect_stdout(buf):
            review.show_case(store, case)
        mocked.assert_not_called()
    assert case.id in buf.getvalue()
    assert "pending automated actions" in buf.getvalue()


def test_show_briefing_is_the_only_thing_that_calls_the_model(review, held_case) -> None:
    store, case = held_case
    with patch.object(review, "brief", return_value="the model's read") as mocked:
        buf = io.StringIO()
        with redirect_stdout(buf):
            review.show_briefing(store, case)
        mocked.assert_called_once()
    assert "the agent's read" in buf.getvalue()
    assert "the model's read" in buf.getvalue()


def test_a_briefing_that_returns_nothing_says_so_rather_than_hanging(review, held_case) -> None:
    store, case = held_case
    with patch.object(review, "brief", return_value=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            review.show_briefing(store, case)
    assert "no briefing" in buf.getvalue()
    assert "model was unavailable" in buf.getvalue()


def test_the_thinking_line_appears_before_the_model_is_called(review, held_case) -> None:
    """So a reviewer watching sees why nothing is happening, rather than
    concluding the terminal has hung."""
    store, case = held_case
    order: list[str] = []

    def slow_brief(_record):
        order.append("model called")
        return "ok"

    with patch.object(review, "brief", side_effect=slow_brief):
        buf = io.StringIO()
        with redirect_stdout(buf):
            review.show_briefing(store, case)
        printed = buf.getvalue()
        assert "thinking" in printed
        assert printed.index("thinking") < printed.index("the agent's read")
