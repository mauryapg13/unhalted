"""The five card scenarios `docs/capturing-fixtures.md` names, each behind a
different published Razorpay test card, shared so `scripts/classify.py` and
`scripts/inject.py` cannot silently disagree about what they mean by them.

`method` is fixed at "card" for all five — that is what the underlying test
cards are, and not a claim about UPI or emandate.

`error_source` used to be one value, "gateway", applied to all five alike.
That was never grounded in anything about each reason specifically — it just
matched the three real captured fixtures, which are all the one generic
`payment_failed` Razorpay's test-mode cards produce regardless of which card
is used (issue #8). These five reasons are different: each has a real,
documented cause, and `core/taxonomy.py`'s own rules already say what it is
— `ERROR_SOURCE` below repeats exactly that, reason by reason, rather than
a placeholder repeated five times. It matters concretely for
`payment_timed_out`: the taxonomy reads a different class for a "customer"
source than a "bank" one, and "gateway" was neither, so injecting it never
actually reached the class the scenario is named for.
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

#: The source `core/taxonomy.py`'s own rule for each reason already names —
#: quoted from `TABLE` there, not invented here:
#:   insufficient_fund       — the customer's own account balance
#:   gateway_technical_error — "partner bank technical issue or downtime;
#:                             either way not the customer"
#:   card_declined           — "declined by the customer's bank"
#:   payment_timed_out       — the scenario `docs/capturing-fixtures.md`
#:                             names is a manual checkout run out of time
#:                             (customer), the taxonomy's other documented
#:                             cause; a real subscription mandate the bank
#:                             failed to debit in time is "bank", not this
#:   authentication_failed   — "the customer was present and did not
#:                             complete it"
ERROR_SOURCE: dict[str, str] = {
    "insufficient_fund": "customer",
    "gateway_technical_error": "gateway",
    "card_declined": "bank",
    "payment_timed_out": "customer",
    "authentication_failed": "customer",
}
