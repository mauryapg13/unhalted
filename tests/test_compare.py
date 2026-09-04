"""One case under both policies.

A single case shows no contrast on its own — the agent declines a futile retry
and a reader with nothing to compare it against sees a system doing nothing.
These assert that the comparison is read from real state on both sides, and
that it stops short of claiming anything about recovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from unhalted import cli
from unhalted.agent import handle_failure
from unhalted.measure.compare import compare, differences
from unhalted.models import DiagnosisClass, FailureSignal
from unhalted.shell.windows import IST, is_execution_allowed
from unhalted.store import Store

#: Inside the 17:00-21:30 restricted band, so the baseline's daily retries all
#: land in one. That is not a contrivance — a blind T+1/T+2/T+3 policy repeats
#: whatever hour the original failure happened at.
NOW = datetime(2026, 9, 3, 17, 30, tzinfo=IST)


def signal(reason="insufficient_funds", **kwargs):
    fields = {
        "payment_id": "pay_CMP", "customer_ref": "cust_cmp", "amount_paise": 49900,
        "occurred_at": NOW, "source": "test", "method": "card",
        "error_reason": reason, "error_source": "customer",
    }
    fields.update(kwargs)
    return FailureSignal(**fields)


def build(sig, path):
    store = Store(path)
    case = handle_failure(store, sig, now=NOW)
    result = compare(
        store.get_case(case.id),
        store.signals(case.id),
        store.latest_diagnosis(case.id),
        store.timeline(case.id),
    )
    store.close()
    return result


@pytest.fixture
def balance(tmp_path):
    return build(signal(), str(tmp_path / "a.db"))


@pytest.fixture
def expired(tmp_path):
    """An expired card: the class where a retry provably cannot work."""
    return build(signal(reason="card_expired"), str(tmp_path / "b.db"))


def test_the_baseline_spends_the_three_retries_razorpay_documents(balance) -> None:
    assert balance.base.attempts == 3


def test_the_agent_schedules_fewer_debits_than_the_baseline(balance) -> None:
    assert balance.agent.attempts < balance.base.attempts


def test_a_retry_that_cannot_work_is_still_spent_by_the_baseline(expired) -> None:
    """The whole argument, on one case: three attempts against a dead mandate."""
    assert expired.diagnosis.klass is DiagnosisClass.MANDATE_STATE_BROKEN
    assert expired.base.futile_attempts == 3
    assert expired.agent.attempts == 0
    assert expired.agent.futile_attempts == 0


def test_both_columns_start_from_the_same_moment(balance) -> None:
    """A captured fixture carries its own clock; two clocks compare nothing."""
    dated = [e for e in balance.events if e.at is not None]
    first_baseline = next(e for e in dated if e.baseline)
    first_agent = next(e for e in dated if e.agent)
    assert first_baseline.at - first_agent.at == timedelta(days=1)


def test_the_baseline_retries_land_inside_a_restricted_band(tmp_path) -> None:
    """Not asserted — checked against the same window rule the shell uses.

    On UPI, because NPCI's execution bands govern UPI Autopay and nothing else.
    """
    upi = build(signal(method="upi"), str(tmp_path / "upi.db"))
    assert upi.base.attempts_in_restricted_window == 3
    for event in upi.events:
        if event.baseline.startswith("DEBIT ATTEMPT"):
            assert not is_execution_allowed(event.at).allowed


def test_a_card_baseline_is_not_charged_with_npci_violations(balance) -> None:
    """The `balance` fixture is a card. Cards do not route through UPI Autopay."""
    assert balance.signal.method == "card"
    assert balance.base.attempts_in_restricted_window == 0


def test_the_agent_moves_a_upi_retry_out_of_a_restricted_band(tmp_path) -> None:
    upi = build(signal(method="upi"), str(tmp_path / "upi2.db"))
    assert upi.agent.attempts_in_restricted_window == 0
    assert upi.agent.corrected_into_window >= 1, "the retry had to be moved"


def test_a_card_retry_is_not_moved_and_records_no_violation(balance) -> None:
    """Issue #30. The audit trail must not say a rule fired that did not apply."""
    assert balance.signal.method == "card"
    assert balance.agent.corrected_into_window == 0
    assert balance.agent.attempts_in_restricted_window == 0


def test_the_baseline_never_contacts_anybody(balance) -> None:
    assert balance.base.contacts == 0


def test_the_agent_side_is_read_from_the_audit_trail(balance) -> None:
    """Not recomputed. If the trail says it happened, the column says it."""
    assert any("case-opened" in e.agent for e in balance.events)
    assert any(e.agent for e in balance.events)


def test_every_counted_difference_is_a_pair_of_counts(balance) -> None:
    for label, agent_n, base_n, _ in differences(balance):
        assert isinstance(agent_n, int), label
        assert isinstance(base_n, int), label


def test_a_case_with_no_signal_does_not_pretend_to_compare(tmp_path) -> None:
    store = Store(str(tmp_path / "c.db"))
    case = handle_failure(store, signal(), now=NOW)
    result = compare(store.get_case(case.id), [], None, store.timeline(case.id))
    store.close()
    assert result.base.attempts == 0
    assert "not comparable" in result.base.outcome


def test_the_comparison_claims_nothing_about_recovery(tmp_path, capsys) -> None:
    """The batch report splits in two for this reason. This has no part two."""
    path = str(tmp_path / "d.db")
    store = Store(path)
    case = handle_failure(store, signal(), now=NOW)
    store.close()

    cli.main(["--db", path, "compare", case.id])
    out = capsys.readouterr().out

    assert "recovered" not in out.lower().replace("what was recovered", "")
    assert "outcome model" in out


def test_the_command_prints_both_policies(tmp_path, capsys) -> None:
    path = str(tmp_path / "e.db")
    store = Store(path)
    case = handle_failure(store, signal(reason="card_expired"), now=NOW)
    store.close()

    assert cli.main(["--db", path, "compare", case.id.removeprefix("CASE-")[:4]]) == 0
    out = capsys.readouterr().out

    assert "Razorpay's retry policy" in out
    assert "unhalted" in out
    assert "DEBIT ATTEMPT 3/3" in out
    assert "cannot work" in out
    assert "Razorpay's own error description rules a retry out" in out
    assert "HALTED" in out


def test_an_unknown_case_is_refused_rather_than_guessed(tmp_path, capsys) -> None:
    path = str(tmp_path / "f.db")
    Store(path).close()
    assert cli.main(["--db", path, "compare", "CASE-NOPE"]) == 1
    assert "no case matching" in capsys.readouterr().out
