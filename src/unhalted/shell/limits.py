"""Monetary ceilings on a recurring debit.

Three limits with two different consequences, which is why the method has to be
known before any of them can be applied:

- **Cards.** Recurring charges above ₹15,000 *fail automatically* for domestic
  cards. Attempting one spends an NPCI retry to no purpose.
- **UPI Autopay.** Debits up to ₹15,000 are frictionless (₹1,00,000 for BFSI).
  Above that the customer must authorise the individual debit — the charge does
  not fail, it *waits for a person*, which is a different recovery path.
- **Emandate.** ₹1,00,00,000.

Separately, and beneath all of them, sits the ceiling the customer actually
agreed to: the mandate's own `max_amount`. Debiting above it is not a limit
breach, it is exceeding consent.

Sourced from Razorpay's subscription settings and supported-payment-methods
references. Recorded in CHECKPOINTS.md.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from unhalted.policy import POLICY

RUPEE = 100  # paise

#: Read from config/policy.yaml — see unhalted.policy.
FRICTIONLESS_UPI = POLICY.frictionless_upi_paise
FRICTIONLESS_UPI_BFSI = POLICY.frictionless_upi_bfsi_paise
UPI_MANDATE_MAX = POLICY.upi_mandate_max_paise
CARD_RECURRING_MAX = POLICY.card_recurring_max_paise
EMANDATE_MAX = POLICY.emandate_max_paise

LIMIT_RULE_VERSION = POLICY.limit_rule_version


class LimitOutcome(str, enum.Enum):
    PERMITTED = "permitted"
    #: The debit would fail at the network. Attempting it wastes a retry.
    WOULD_FAIL = "would-fail"
    #: The debit is allowed but needs the customer to authorise this one.
    NEEDS_ADDITIONAL_AUTH = "needs-additional-auth"
    #: Above what the customer consented to when the mandate was created.
    EXCEEDS_MANDATE = "exceeds-mandate"


@dataclass(frozen=True)
class LimitCheck:
    outcome: LimitOutcome
    reason: str
    rule_version: str = LIMIT_RULE_VERSION

    @property
    def may_attempt(self) -> bool:
        return self.outcome is LimitOutcome.PERMITTED

    @property
    def code(self) -> str | None:
        return None if self.may_attempt else f"LIMIT:{self.outcome.value.upper().replace('-', '_')}"


def check(
    amount_paise: int,
    method: str | None,
    *,
    mandate_max_paise: int | None = None,
    bfsi: bool = False,
) -> LimitCheck:
    """Whether this debit may be attempted at all.

    The mandate's own ceiling is checked first. A network limit is a rule about
    what will work; the mandate ceiling is a rule about what the customer agreed
    to, and consent outranks feasibility.
    """
    if amount_paise <= 0:
        return LimitCheck(LimitOutcome.WOULD_FAIL, "amount must be positive")

    if mandate_max_paise is not None and amount_paise > mandate_max_paise:
        return LimitCheck(
            LimitOutcome.EXCEEDS_MANDATE,
            f"₹{amount_paise / RUPEE:,.0f} is above the mandate's registered ceiling of "
            f"₹{mandate_max_paise / RUPEE:,.0f}; the customer never agreed to this amount",
        )

    m = (method or "").lower()

    if m == "card":
        if amount_paise > CARD_RECURRING_MAX:
            return LimitCheck(
                LimitOutcome.WOULD_FAIL,
                f"₹{amount_paise / RUPEE:,.0f} is above the ₹{CARD_RECURRING_MAX / RUPEE:,.0f} "
                "recurring-card limit; a domestic card charge above it fails automatically",
            )
        return LimitCheck(LimitOutcome.PERMITTED, "within the recurring-card limit")

    if m == "upi":
        if amount_paise > UPI_MANDATE_MAX:
            return LimitCheck(
                LimitOutcome.WOULD_FAIL,
                f"₹{amount_paise / RUPEE:,.0f} is above the ₹{UPI_MANDATE_MAX / RUPEE:,.0f} "
                "UPI Autopay mandate ceiling",
            )
        frictionless = FRICTIONLESS_UPI_BFSI if bfsi else FRICTIONLESS_UPI
        if amount_paise > frictionless:
            return LimitCheck(
                LimitOutcome.NEEDS_ADDITIONAL_AUTH,
                f"₹{amount_paise / RUPEE:,.0f} is above the frictionless UPI limit of "
                f"₹{frictionless / RUPEE:,.0f}; the customer must authorise this debit. "
                "It has not failed — it is waiting for a person",
            )
        return LimitCheck(LimitOutcome.PERMITTED, "within the frictionless UPI limit")

    if m in ("emandate", "nach", "netbanking"):
        if amount_paise > EMANDATE_MAX:
            return LimitCheck(
                LimitOutcome.WOULD_FAIL,
                f"₹{amount_paise / RUPEE:,.0f} is above the emandate ceiling",
            )
        return LimitCheck(LimitOutcome.PERMITTED, "within the emandate ceiling")

    # An unknown method gets the strictest ceiling we know of rather than a pass.
    if amount_paise > FRICTIONLESS_UPI:
        return LimitCheck(
            LimitOutcome.WOULD_FAIL,
            f"method {method!r} is unrecognised, so the strictest known ceiling "
            f"(₹{FRICTIONLESS_UPI / RUPEE:,.0f}) applies rather than none",
        )
    return LimitCheck(LimitOutcome.PERMITTED, f"method {method!r} unrecognised but amount is small")
