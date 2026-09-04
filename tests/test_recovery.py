"""The other side of the loop resume_after_review opens: the customer paid
through the recovery link, and the case is finished, not merely resumed.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from unhalted.agent import handle_failure, mark_recovered
from unhalted.models import CaseState, FailureSignal
from unhalted.shell.windows import IST
from unhalted.store import Store

NOW = datetime(2026, 9, 4, 11, 0, tzinfo=IST)


@pytest.fixture
def open_case(tmp_path):
    store = Store(str(tmp_path / "recovery.db"))
    signal = FailureSignal(
        payment_id="pay_RECOVER", customer_ref="cust_recover", amount_paise=49900,
        occurred_at=NOW, source="test", method="card",
        error_reason="insufficient_funds", error_source="customer",
    )
    case = handle_failure(store, signal, now=NOW)
    yield store, case
    store.close()


def test_recovery_sets_the_case_to_recovered(open_case) -> None:
    store, case = open_case
    mark_recovered(store, case.id, payment_id="pay_XYZ", amount_paise=49900, now=NOW)
    assert store.get_case(case.id).state is CaseState.RECOVERED


def test_recovery_cancels_the_scheduled_retry(open_case) -> None:
    store, case = open_case
    # A balance failure schedules both halves: the question asking when to
    # try, and the fallback retry behind it. A recovery cancels both.
    assert len(store.pending_actions(case_id=case.id)) == 2

    mark_recovered(store, case.id, payment_id="pay_XYZ", amount_paise=49900, now=NOW)

    assert store.pending_actions(case_id=case.id) == []


def test_recovery_is_recorded_with_the_real_payment_it_closed_against(open_case) -> None:
    store, case = open_case
    mark_recovered(store, case.id, payment_id="pay_XYZ", amount_paise=49900, now=NOW)

    record = next(r for r in store.timeline(case.id) if r.decision_type == "recovery")
    assert record.action == "paid via recovery link"
    assert record.inputs["payment_id"] == "pay_XYZ"
    assert record.inputs["amount_paise"] == 49900
    assert "cancelled" in record.outcome


def test_recovery_returns_how_many_pending_actions_it_cancelled(open_case) -> None:
    store, case = open_case
    cancelled = mark_recovered(store, case.id, payment_id="pay_XYZ", amount_paise=49900, now=NOW)
    assert cancelled == 2


def test_the_customer_is_told_it_settled(open_case, capsys) -> None:
    """Before this, a case went RECOVERED and the customer heard nothing —
    the same silence a real WhatsApp thread never would."""
    store, case = open_case
    mark_recovered(store, case.id, payment_id="pay_XYZ", amount_paise=49900, now=NOW)

    out = capsys.readouterr().out
    assert "received" in out
    assert "499" in out
