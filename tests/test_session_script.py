"""The customer terminal must not replay one case forever.

`real_signal` used to always read the alphabetically first captured fixture,
so a database that already held a case for it looked, on the next run, like
the demo could only ever produce that one case — the three fixtures are three
distinct real payments, never reached. This is the regression test for
walking past whichever ones the database has already seen.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime

import pytest

from unhalted.agent import handle_failure
from unhalted.shell.windows import IST
from unhalted.store import Store

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "session.py"
NOW = datetime(2026, 9, 3, 11, 0, tzinfo=IST)


@pytest.fixture(scope="module")
def session():
    spec = importlib.util.spec_from_file_location("session_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "session.db"))
    yield s
    s.close()


def test_a_fresh_database_gets_the_first_fixture(session, store) -> None:
    signal = session.real_signal(store)
    assert signal.payment_id == "pay_TWtdZSPcdnn9sv"


def test_a_database_that_has_seen_one_gets_the_next(session, store) -> None:
    first = session.real_signal(store)
    handle_failure(store, first, now=NOW)

    second = session.real_signal(store)
    assert second.payment_id != first.payment_id


def test_running_it_three_times_produces_three_distinct_cases(session, store) -> None:
    seen = set()
    for _ in range(3):
        signal = session.real_signal(store)
        assert signal.payment_id not in seen, "the same payment came back early"
        seen.add(signal.payment_id)
        handle_failure(store, signal, now=NOW)
    assert len(seen) == 3


def test_once_every_fixture_has_a_case_it_replays_rather_than_erroring(session, store) -> None:
    for _ in range(3):
        handle_failure(store, session.real_signal(store), now=NOW)

    # A fourth call must not crash for want of an unseen payment.
    fourth = session.real_signal(store)
    assert fourth.payment_id is not None
