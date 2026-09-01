"""Monetary ceilings, and the fact that they have different consequences.

Three limits, two outcomes. A card above its ceiling *fails* — attempting it
spends an NPCI retry to no purpose. A UPI debit above the frictionless limit
does not fail; it waits for the customer to authorise that specific debit, which
is a different recovery path entirely. Treating those two the same is how you
either waste attempts or abandon recoverable money.
"""

from __future__ import annotations

import pytest

from unhalted.shell.limits import (
    CARD_RECURRING_MAX,
    EMANDATE_MAX,
    FRICTIONLESS_UPI,
    FRICTIONLESS_UPI_BFSI,
    RUPEE,
    LimitOutcome,
    check,
)


def rupees(n: int) -> int:
    return n * RUPEE


# -- the mandate's own ceiling outranks everything ----------------------------


def test_the_mandate_ceiling_is_checked_before_any_network_limit() -> None:
    """Consent outranks feasibility. ₹500 is fine for a card and not fine here."""
    c = check(rupees(500), "card", mandate_max_paise=rupees(200))
    assert c.outcome is LimitOutcome.EXCEEDS_MANDATE
    assert not c.may_attempt
    assert "never agreed" in c.reason


def test_a_debit_at_exactly_the_mandate_ceiling_is_permitted() -> None:
    assert check(rupees(200), "card", mandate_max_paise=rupees(200)).may_attempt


# -- cards fail; UPI waits ----------------------------------------------------


def test_a_card_above_its_recurring_limit_would_fail() -> None:
    c = check(CARD_RECURRING_MAX + 1, "card")
    assert c.outcome is LimitOutcome.WOULD_FAIL
    assert "fails automatically" in c.reason


def test_a_card_at_its_limit_is_permitted() -> None:
    assert check(CARD_RECURRING_MAX, "card").may_attempt


def test_upi_above_the_frictionless_limit_needs_authorisation_not_a_retry() -> None:
    """The distinction that matters: this has not failed."""
    c = check(FRICTIONLESS_UPI + 1, "upi")
    assert c.outcome is LimitOutcome.NEEDS_ADDITIONAL_AUTH
    assert "waiting for a person" in c.reason
    assert not c.may_attempt


def test_a_bfsi_mandate_gets_the_higher_frictionless_limit() -> None:
    amount = FRICTIONLESS_UPI + rupees(1000)
    assert check(amount, "upi").outcome is LimitOutcome.NEEDS_ADDITIONAL_AUTH
    assert check(amount, "upi", bfsi=True).may_attempt
    assert check(FRICTIONLESS_UPI_BFSI + 1, "upi", bfsi=True).outcome is not LimitOutcome.PERMITTED


def test_upi_above_the_mandate_creation_ceiling_would_fail() -> None:
    assert check(rupees(1_00_001), "upi").outcome is LimitOutcome.WOULD_FAIL


def test_emandate_reaches_a_crore() -> None:
    assert check(EMANDATE_MAX, "emandate").may_attempt
    assert check(EMANDATE_MAX + 1, "emandate").outcome is LimitOutcome.WOULD_FAIL


# -- the unknown case ---------------------------------------------------------


def test_an_unrecognised_method_gets_the_strictest_ceiling_not_a_pass() -> None:
    """Failing open on an unknown method would let any amount through."""
    assert check(rupees(50_000), "carrier_pigeon").outcome is LimitOutcome.WOULD_FAIL
    assert check(rupees(100), "carrier_pigeon").may_attempt


def test_a_missing_method_is_treated_as_unknown() -> None:
    assert check(rupees(50_000), None).outcome is LimitOutcome.WOULD_FAIL


@pytest.mark.parametrize("amount", [0, -1, -49900])
def test_a_non_positive_amount_is_refused(amount: int) -> None:
    assert check(amount, "card").outcome is LimitOutcome.WOULD_FAIL


def test_every_refusal_carries_a_code_and_a_rule_version() -> None:
    c = check(CARD_RECURRING_MAX + 1, "card")
    assert c.code == "LIMIT:WOULD_FAIL"
    assert c.rule_version
    assert check(rupees(100), "card").code is None
