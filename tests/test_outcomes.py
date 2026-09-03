"""The money argument, and what it is not allowed to claim.

These assert properties rather than figures. A test that pins 0.389% would go
red every time the taxonomy improves, which is the wrong signal — the claim is
that breakeven is arithmetic on real costs, not that it equals any number.
"""

from __future__ import annotations

import pytest

from unhalted.measure.outcomes import (
    Exposure,
    breakeven,
    classify,
    envelope,
    render_outcomes,
)
from unhalted.models import DiagnosisClass as D
from unhalted.shell.ladder import LADDER, Rung

RUPEE = 100


def book(broken=0, revoked=0, unknown=0, contested=0, amount=50_000):
    """A book of cases, all the same size, sorted by class."""
    items = []
    for klass, n in (
        (D.MANDATE_STATE_BROKEN, broken),
        (D.CUSTOMER_INTENT_REVOKED, revoked),
        (D.UNKNOWN, unknown),
        (D.RECOVERABLE_BALANCE, contested),
    ):
        items.extend([(amount, klass)] * n)
    return items


def test_every_rupee_lands_in_exactly_one_bucket() -> None:
    e = classify(book(broken=3, revoked=2, unknown=4, contested=11))
    assert e.cases == 20
    parts = (e.unreachable_paise + e.must_not_take_paise
             + e.unclassified_paise + e.contested_paise)
    assert parts == e.total_paise


def test_a_dead_mandate_is_unreachable_and_a_balance_failure_is_not() -> None:
    """Razorpay's descriptions decide this, not a judgement about customers."""
    e = classify(book(broken=1, contested=1))
    assert e.unreachable_cases == 1
    assert e.contested_cases == 1


def test_breakeven_is_the_cost_divided_by_what_it_is_placed_in_front_of() -> None:
    e = classify(book(broken=10, amount=50_000))
    be = breakeven(e)
    expected = (10 * LADDER[Rung.REAUTHORISATION].cost_paise) / (10 * 50_000)
    assert be.rate == pytest.approx(expected)
    assert be.spend_paise == 10 * LADDER[Rung.REAUTHORISATION].cost_paise


def test_breakeven_falls_as_the_amounts_rise() -> None:
    """The same ₹2 link against a larger bill is a smaller bar to clear."""
    cheap = breakeven(classify(book(broken=10, amount=2_000)))
    dear = breakeven(classify(book(broken=10, amount=200_000)))
    assert dear.rate < cheap.rate
    assert dear.leverage > cheap.leverage


def test_customers_needed_can_be_less_than_one() -> None:
    """The clearest way to say it: one payer covers the whole campaign."""
    be = breakeven(classify(book(broken=50, amount=50_000)))
    assert 0 < be.customers_needed < 1


def test_the_agent_ceiling_exceeds_the_baseline_by_exactly_the_unreachable_money() -> None:
    """The dominance claim. The magnitude is unknown; the ordering is not."""
    e = classify(book(broken=7, revoked=3, unknown=2, contested=9))
    env = envelope(e)
    gap = env.agent_ceiling_paise - env.baseline_ceiling_paise
    assert gap == e.unreachable_paise == env.dominance_paise
    assert gap > 0


def test_revoked_money_is_in_neither_ceiling() -> None:
    """Recovering it would be the failure, not the success."""
    e = classify(book(revoked=5, contested=5))
    env = envelope(e)
    assert env.agent_ceiling_paise == e.contested_paise
    assert env.baseline_ceiling_paise == e.contested_paise


def test_an_empty_book_does_not_divide_by_zero() -> None:
    e = classify([])
    be, env = breakeven(e), envelope(e)
    assert be.rate == 0.0
    assert be.customers_needed == 0.0
    assert be.leverage == 0.0
    assert env.dominance_paise == 0
    assert e.share(0) == 0.0


def test_a_book_with_nothing_unreachable_has_no_breakeven_to_clear() -> None:
    be = breakeven(classify(book(contested=10)))
    assert be.cases == 0
    assert be.spend_paise == 0
    assert be.rate == 0.0


def test_the_report_never_claims_a_recovery() -> None:
    """The whole reason this module exists rather than an outcome model."""
    e = classify(book(broken=5, revoked=1, unknown=2, contested=12))
    text = render_outcomes(e, breakeven(e), envelope(e))

    assert "NOT REPORTED" in text
    assert "Rupees recovered" in text
    assert "needs real outcomes at volume" in text
    for claim in ("we recovered", "recovered Rs", "revenue recovered"):
        assert claim.lower() not in text.lower()


def test_the_report_refuses_to_call_contested_money_recoverable() -> None:
    """'A retry is not ruled out' is not 'the money comes back'."""
    e = classify(book(broken=2, contested=8))
    text = render_outcomes(e, breakeven(e), envelope(e))
    assert "not 'recoverable'" in text
    assert "Ceilings, not estimates" in envelope(e).notes[0]


def test_shares_are_reported_against_the_whole_book() -> None:
    e: Exposure = classify(book(broken=1, contested=3, amount=10_000))
    assert e.share(e.unreachable_paise) == pytest.approx(0.25)
