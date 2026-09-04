"""A signal already diagnosed must not be diagnosed and scheduled again.

Razorpay redelivers, "belt and braces" delivers the same payment under a new
event id, and re-running a demo script sends the same payment twice — all
three must land on the one case that already exists and change nothing
further. `handle_failure` used to gate this on whether *this* call happened
to be the one that created the case row, which is a different question from
whether the case had actually been worked yet: `ingest/webhooks.py` creates
the row itself, for durability, before ever calling `handle_failure`, so from
inside that function the row always looked pre-existing — including on a
payment's genuine first-ever delivery. That mislabelled the very first
delivery as a repeat, and — the part that matters for money — let every
*true* repeat fall through into scheduling a second retry for a failure that
happened once.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from unhalted.agent import handle_failure
from unhalted.models import FailureSignal
from unhalted.shell.windows import IST
from unhalted.store import Store

NOW = datetime(2026, 9, 4, 11, 0, tzinfo=IST)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "redelivery.db"))
    yield s
    s.close()


def signal(**over):
    fields = {
        "payment_id": "pay_REDELIVERED", "customer_ref": "cust_re", "amount_paise": 49900,
        "occurred_at": NOW, "source": "test", "method": "card",
        "error_reason": "insufficient_funds", "error_source": "customer",
    }
    fields.update(over)
    return FailureSignal(**fields)


def test_the_first_call_is_recorded_as_case_opened(store) -> None:
    case = handle_failure(store, signal(), now=NOW)
    ingest = next(r for r in store.timeline(case.id) if r.decision_type == "ingest")
    assert ingest.action == "case-opened"


def test_calling_it_again_with_the_same_signal_does_not_schedule_twice(store) -> None:
    first = handle_failure(store, signal(), now=NOW)
    handle_failure(store, signal(), now=NOW)

    assert len(store.pending_actions(case_id=first.id)) == 1


def test_the_second_call_is_recorded_as_already_known_not_a_second_opening(store) -> None:
    handle_failure(store, signal(), now=NOW)
    case = handle_failure(store, signal(), now=NOW)

    ingests = [r for r in store.timeline(case.id) if r.decision_type == "ingest"]
    assert len(ingests) == 2
    assert ingests[0].action == "case-opened"
    assert ingests[1].action == "signal already known; case is open"


def test_the_second_call_does_not_diagnose_again(store) -> None:
    handle_failure(store, signal(), now=NOW)
    case = handle_failure(store, signal(), now=NOW)

    diagnoses = [r for r in store.timeline(case.id) if r.decision_type == "diagnosis"]
    assert len(diagnoses) == 1


def test_pre_creating_the_case_before_calling_handle_failure_still_reports_it_as_opened(
    store,
) -> None:
    """The exact shape of the bug: `ingest/webhooks.py` calls `store.open_case`
    itself, for durability, before `handle_failure` ever runs — so the case row
    already exists by the time `handle_failure` looks. That must not read as a
    repeat."""
    sig = signal()
    store.open_case(sig)  # simulates webhooks.py's pre-creation step

    case = handle_failure(store, sig, now=NOW)

    ingest = next(r for r in store.timeline(case.id) if r.decision_type == "ingest")
    assert ingest.action == "case-opened"
    assert len(store.pending_actions(case_id=case.id)) == 1
