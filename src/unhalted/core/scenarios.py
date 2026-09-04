"""The five card scenarios `docs/capturing-fixtures.md` names, each behind a
different published Razorpay test card, shared so `scripts/classify.py` and
`scripts/inject.py` cannot silently disagree about what they mean by them.

`error_source` is "gateway" throughout: all five are a card checkout
declining, the same as the three fixtures already captured on this account.
Not a claim about UPI or emandate, which is why `method` is fixed at "card"
everywhere this is used.
"""

from __future__ import annotations

#: (error_reason, a plain gloss of what Razorpay's docs say it means).
SCENARIOS: list[tuple[str, str]] = [
    ("insufficient_fund", "the account did not have enough funds"),
    ("gateway_technical_error", "partner bank downtime or a technical issue"),
    ("card_declined", "declined by the bank, no cause stated"),
    ("payment_timed_out", "the customer exceeded the payment time limit"),
    ("authentication_failed", "OTP or 3DS was not completed"),
]

METHOD = "card"
ERROR_SOURCE = "gateway"
