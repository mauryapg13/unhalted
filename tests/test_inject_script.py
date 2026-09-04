"""A real, audited case from a documented scenario — not a webhook, and it
must never pretend to be one, but everything after it (diagnosis, scheduling,
the audit trail) has to be as real as if it had arrived over one.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime
from unittest.mock import patch

import pytest

from unhalted.agent import handle_failure
from unhalted.core.scenarios import SCENARIOS
from unhalted.models import FailureSignal
from unhalted.shell.windows import IST
from unhalted.store import Store

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "inject.py"
NOW = datetime(2026, 9, 4, 11, 0, tzinfo=IST)


@pytest.fixture(scope="module")
def inject():
    spec = importlib.util.spec_from_file_location("inject_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "inject.db"))
    yield s
    s.close()


def test_every_scenario_is_a_known_reason(inject) -> None:
    """The script's own list must not drift from the shared source of truth."""
    assert set(inject.SCENARIOS) == set(SCENARIOS)


def test_choosing_by_number_returns_the_matching_reason(inject) -> None:
    known = dict(inject.SCENARIOS)
    with patch("builtins.input", return_value="1"):
        assert inject.choose(known) == inject.SCENARIOS[0][0]


def test_choosing_by_name_works_too(inject) -> None:
    known = dict(inject.SCENARIOS)
    with patch("builtins.input", return_value="authentication_failed"):
        assert inject.choose(known) == "authentication_failed"


def test_a_blank_answer_exits_without_picking_anything(inject) -> None:
    known = dict(inject.SCENARIOS)
    with patch("builtins.input", return_value=""):
        assert inject.choose(known) is None


def test_eof_exits_the_same_way_a_blank_answer_does(inject) -> None:
    known = dict(inject.SCENARIOS)
    with patch("builtins.input", side_effect=EOFError):
        assert inject.choose(known) is None


def test_an_out_of_range_number_is_refused_not_misread(inject) -> None:
    known = dict(inject.SCENARIOS)
    with patch("builtins.input", return_value="99"):
        assert inject.choose(known) is None


def test_gibberish_is_refused(inject) -> None:
    known = dict(inject.SCENARIOS)
    with patch("builtins.input", return_value="not a real thing"):
        assert inject.choose(known) is None


def test_injecting_one_produces_a_real_diagnosed_case(inject, store) -> None:
    signal = FailureSignal(
        payment_id="pay_INJECTED_insufficient_fund", customer_ref="cust_injected_insufficient_fund",
        amount_paise=inject.AMOUNT_PAISE, occurred_at=NOW, source="inject",
        method=inject.METHOD, error_reason="insufficient_fund",
        error_source=inject.ERROR_SOURCE["insufficient_fund"],
    )
    case = handle_failure(store, signal, now=NOW)

    diagnosis = store.latest_diagnosis(case.id)
    assert diagnosis is not None
    assert diagnosis.klass.value == "recoverable-balance"
    # An empty account asks the customer when to try rather than guessing:
    # the question now, and a fallback retry behind it for a silent customer.
    assert [a["kind"] for a in store.pending_actions(case_id=case.id)] == ["nudge", "retry"]


def test_the_same_reason_run_twice_matches_back_rather_than_duplicating(inject, store) -> None:
    def one():
        return handle_failure(
            store,
            FailureSignal(
                payment_id="pay_INJECTED_card_declined", customer_ref="cust_injected_card_declined",
                amount_paise=inject.AMOUNT_PAISE, occurred_at=NOW, source="inject",
                method=inject.METHOD, error_reason="card_declined",
                error_source=inject.ERROR_SOURCE["card_declined"],
            ),
            now=NOW,
        )

    first = one()
    second = one()
    assert first.id == second.id
    assert len(store.pending_actions(case_id=first.id)) == 1
