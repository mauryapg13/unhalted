"""The compliance lint.

This project offers nothing, so any offer in a draft was invented by the model.
The first version of this check missed "20% off next month" — the
specification's own example — because it looked for the word "discount". These
tests exist so that stays fixed.
"""

from __future__ import annotations

import pytest

from unhalted.shell.lint import check

GOOD = "Hi - your Acme payment of Rs 499 didn't go through. Reply STOP to opt out."


def lint(draft: str):
    return check(draft, amount_paise=49900, merchant="Acme")


def test_a_plain_factual_reminder_passes() -> None:
    assert lint(GOOD).passed


@pytest.mark.parametrize(
    "draft",
    [
        "Pay today and get 20% off next month! Acme Rs 499. Reply STOP.",
        "Acme Rs 499 - 20 per cent off if you pay today. Reply STOP.",
        "Acme Rs 499. Settle now and we'll waive next month. Reply STOP.",
        "Acme Rs 499 due. Pay now for a free month. Reply STOP.",
        "Acme Rs 499. Here's a coupon for your trouble. Reply STOP.",
        "Acme Rs 499 - 50% cashback on payment. Reply STOP.",
    ],
)
def test_every_invented_offer_is_blocked(draft: str) -> None:
    result = lint(draft)
    assert not result.passed, f"an invented offer got through: {draft}"


@pytest.mark.parametrize(
    "draft",
    [
        "Acme Rs 499 overdue. Legal action will follow. Reply STOP.",
        "Acme Rs 499. Pay or face penalties. Reply STOP.",
        "Acme Rs 499. This will affect your credit score. Reply STOP.",
        "Acme Rs 499. A recovery agent will visit. Reply STOP.",
    ],
)
def test_threats_are_blocked(draft: str) -> None:
    assert not lint(draft).passed


@pytest.mark.parametrize(
    "draft",
    [
        "Acme Rs 499. Act now! Reply STOP.",
        "Acme Rs 499. Final warning. Reply STOP.",
        "Acme Rs 499. Last chance to pay. Reply STOP.",
    ],
)
def test_manufactured_urgency_is_blocked(draft: str) -> None:
    assert not lint(draft).passed


def test_a_promise_the_agent_cannot_make_is_blocked() -> None:
    assert not lint("Acme Rs 499. We guarantee no further charges. Reply STOP.").passed


# -- things that must be present ------------------------------------------


def test_a_message_without_the_amount_is_blocked() -> None:
    result = lint("Your Acme payment failed. Reply STOP.")
    assert not result.passed
    assert any("amount" in m for m in result.missing)


def test_a_message_without_the_merchant_is_blocked() -> None:
    """The customer needs to know who is asking them for money."""
    result = lint("Your payment of Rs 499 failed. Reply STOP.")
    assert not result.passed
    assert any("merchant" in m for m in result.missing)


def test_a_message_with_no_way_to_stop_is_blocked() -> None:
    result = lint("Your Acme payment of Rs 499 failed.")
    assert not result.passed
    assert any("stop" in m for m in result.missing)


@pytest.mark.parametrize("phrasing", ["Reply STOP", "reply stop", "unsubscribe", "opt out",
                                      "opt-out"])
def test_any_reasonable_opt_out_phrasing_is_accepted(phrasing: str) -> None:
    assert lint(f"Your Acme payment of Rs 499 failed. {phrasing}.").passed


def test_the_violation_is_reported_so_it_can_be_logged() -> None:
    """A blocked draft has to be visible, not silently discarded."""
    result = lint("Acme Rs 499. Get 20% off. Reply STOP.")
    assert "percentage" in result.summary or "offer" in result.summary
    assert result.rule_version
