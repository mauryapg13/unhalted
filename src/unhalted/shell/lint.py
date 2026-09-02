"""Compliance lint. The last thing between a drafted message and a customer.

A model writing customer-facing copy will eventually write something that is not
true. The specification's example is a discount nobody authorised, and this
project offers none at all — so any offer language is invented by definition and
is blocked outright rather than checked against a catalogue.

This is a deny-list, and deny-lists leak. A determined paraphrase gets through.
It is here because the alternative is nothing, and because the failures it does
catch are the ones a model actually makes: the confident, plausible, invented
sweetener. Every block is logged with the offending draft so the misses become
visible rather than theoretical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LINT_RULE_VERSION = "compliance-lint-2026-09"

#: This project makes no offers. Any of these in a draft was invented.
INVENTED_OFFER = re.compile(
    r"\b(discount|cashback|coupon|promo(?:tion)?|voucher|bonus|reward|"
    r"free\s+\w+|waive[dr]?|off\s+(?:your\s+)?(?:the\s+)?next|"
    r"refund\s+guarantee)\b",
    re.IGNORECASE,
)

#: A percentage in a payment-recovery message is an offer. There is no honest
#: reason to quote somebody a percentage while asking them to pay what they owe,
#: and unlike a word-list this is hard to paraphrase around.
#:
#: The first version of this check missed "20% off next month" — the
#: specification's own example — because it was looking for the word "discount".
#: A deny-list catches the phrasings you thought of; this catches the shape.
PERCENTAGE = re.compile(r"\d+\s*(?:%|per\s*cent|percent)", re.IGNORECASE)

#: Threats and pressure. Recovery is not collections.
THREAT = re.compile(
    r"\b(legal\s+action|lawyer|court|police|penalt(y|ies)|fine[sd]?|"
    r"blacklist|credit\s+score|recovery\s+agent|consequences)\b",
    re.IGNORECASE,
)

#: Manufactured urgency. A real deadline is a fact; these are pressure.
FALSE_URGENCY = re.compile(
    r"\b(act\s+now|last\s+chance|final\s+warning|immediately\s+or|"
    r"within\s+\d+\s+hours?\s+or|hurry|urgent(ly)?)\b",
    re.IGNORECASE,
)

#: Commitments the agent has no authority to make.
UNAUTHORISED_PROMISE = re.compile(
    r"\b(we\s+guarantee|guaranteed|we\s+promise|assured|no\s+questions\s+asked)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LintResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    rule_version: str = LINT_RULE_VERSION

    @property
    def summary(self) -> str:
        parts = []
        if self.violations:
            parts.append("contains " + "; ".join(self.violations))
        if self.missing:
            parts.append("missing " + ", ".join(self.missing))
        return ". ".join(parts) or "passed"


def check(
    draft: str,
    *,
    amount_paise: int,
    merchant: str,
    require_opt_out: bool = True,
) -> LintResult:
    """Whether a drafted message may be sent.

    Two kinds of failure, and both block. Something present that must not be —
    an invented offer, a threat, manufactured urgency, a promise nobody
    authorised. Or something absent that must be there: the amount, the merchant
    the customer is being asked to pay, and a way to make the messages stop.
    """
    violations: list[str] = []
    missing: list[str] = []

    for label, pattern in (
        ("an offer that was never authorised", INVENTED_OFFER),
        ("a percentage, which in a payment reminder means an offer", PERCENTAGE),
        ("a threat", THREAT),
        ("manufactured urgency", FALSE_URGENCY),
        ("a commitment the agent cannot make", UNAUTHORISED_PROMISE),
    ):
        found = pattern.findall(draft)
        if found:
            words = {(m if isinstance(m, str) else m[0]).lower() for m in found}
            violations.append(f"{label} ({', '.join(sorted(w for w in words if w))})")

    rupees = amount_paise / 100
    amount_forms = {f"{rupees:.0f}", f"{rupees:,.0f}", f"{rupees:.2f}"}
    if not any(form in draft for form in amount_forms):
        missing.append(f"the amount (Rs {rupees:.0f})")

    if merchant.lower() not in draft.lower():
        missing.append(f"the merchant name ({merchant})")

    if require_opt_out and not re.search(r"\b(stop|opt[\s-]?out|unsubscribe)\b", draft, re.IGNORECASE):
        missing.append("a way to stop receiving these")

    return LintResult(passed=not violations and not missing,
                      violations=violations, missing=missing)
