"""Razorpay payloads to the pipeline's own shape.

Everything downstream consumes `FailureSignal` and never sees a Razorpay
payload. That is what lets a second source be added — `subscription.pending` if
that product is ever entitled, or a replayed capture — without touching
diagnosis, scheduling or measurement.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from unhalted.models import FailureSignal


class UnsupportedEvent(ValueError):
    """The payload is well-formed but is not a failure this pipeline handles."""


def pseudonymise(value: str) -> str:
    """A stable, non-reversible reference for a customer identifier.

    Raw contacts and VPAs are personal data. The pipeline only ever needs to
    know that two failures belong to the same person, which a digest gives us
    without storing the identifier itself.
    """
    return "cust_" + hashlib.sha256(value.encode()).hexdigest()[:16]


def from_payment_failed(event: dict[str, Any]) -> FailureSignal:
    """Normalise a Razorpay `payment.failed` webhook event."""
    name = event.get("event")
    if name != "payment.failed":
        raise UnsupportedEvent(f"expected payment.failed, got {name!r}")

    try:
        payment = event["payload"]["payment"]["entity"]
    except (KeyError, TypeError) as exc:
        raise UnsupportedEvent("payload.payment.entity missing") from exc

    payment_id = payment.get("id")
    if not payment_id:
        raise UnsupportedEvent("payment entity has no id")

    amount = payment.get("amount")
    if not isinstance(amount, int):
        raise UnsupportedEvent(f"payment amount is not an integer: {amount!r}")

    identifier = (
        payment.get("customer_id")
        or payment.get("vpa")
        or payment.get("contact")
        or payment.get("email")
        or payment_id
    )
    customer_ref = (
        identifier if str(identifier).startswith("cust_") else pseudonymise(str(identifier))
    )

    created = payment.get("created_at") or event.get("created_at")
    occurred_at = (
        datetime.fromtimestamp(created, tz=UTC)
        if isinstance(created, int)
        else datetime.now(tz=UTC)
    )

    return FailureSignal(
        payment_id=payment_id,
        order_id=payment.get("order_id"),
        customer_ref=customer_ref,
        amount_paise=amount,
        currency=payment.get("currency") or "INR",
        method=payment.get("method"),
        error_code=payment.get("error_code"),
        error_reason=payment.get("error_reason"),
        error_source=payment.get("error_source"),
        error_step=payment.get("error_step"),
        occurred_at=occurred_at,
        token_id=payment.get("token_id"),
        source="razorpay:payment.failed",
        raw=payment,
    )
