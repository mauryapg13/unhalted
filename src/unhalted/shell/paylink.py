"""Give the customer a way to pay that isn't the mandate that just failed.

The ladder already prices this — rung 2 is "message with a pay link", rung 3
is "re-authorisation link", named as "the only path when the mandate itself
is the problem" — but naming a rung is not building it. A nudge with no link
in it gives someone whose card expired nothing to do but wait for a debit
that cannot succeed, when they may well be sitting there with a different
card or a different account they'd rather pay from.

Verified against Razorpay's own documentation (`api/payments/payment-links/
create-standard.md`), not assumed: `POST /v1/payment_links`, Basic Auth on
`key_id:key_secret`, and the URL a customer would actually open comes back as
`short_url`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from unhalted import config

log = logging.getLogger("unhalted.shell.paylink")

TIMEOUT_SECONDS = 15

API_URL = "https://api.razorpay.com/v1/payment_links/"


@dataclass(frozen=True)
class PaymentLink:
    url: str
    id: str
    status: str


def create_payment_link(
    *, amount_paise: int, description: str, contact: str | None = None,
) -> PaymentLink | None:
    """A real, payable link for this amount, or `None`.

    `None` on any failure — no key configured, Razorpay refuses the request,
    the network is down — and every case is logged, not raised. A nudge is not
    worth blocking over a link failing to generate; it goes out without one
    and says so, the same way a briefing that never arrives says so rather
    than the reviewer waiting on it forever.
    """
    key_id = config.razorpay_key_id()
    key_secret = config.razorpay_key_secret()
    if not key_id or not key_secret:
        log.warning("payment link not created: RAZORPAY_KEY_ID/SECRET not configured")
        return None

    body: dict[str, object] = {
        "amount": amount_paise,
        "currency": "INR",
        "description": description,
        # We deliver the message ourselves through the existing channel; Razorpay
        # notifying too would be a second, unrequested message to the customer.
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
    }
    if contact:
        body["customer"] = {"contact": contact}

    try:
        r = httpx.post(
            API_URL,
            auth=(key_id, key_secret),
            json=body,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        log.warning("payment link request failed: %s", e)
        return None

    if r.status_code != 200:
        log.warning("payment link refused: HTTP %s %s", r.status_code, r.text[:300])
        return None

    payload = r.json()
    short_url = payload.get("short_url")
    if not short_url:
        log.warning("payment link response carried no short_url: %s", payload)
        return None

    return PaymentLink(url=short_url, id=payload.get("id", ""), status=payload.get("status", ""))
