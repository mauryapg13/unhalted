"""Checking whether a failure was really a failure.

Razorpay documents this and calls it expected: a `payment.failed` webhook can be
followed by `payment.captured` for the same transaction, because the customer
retried inside their UPI app and succeeded. The first payment stays failed
forever; a second one on the same order goes through.

So the question is not "did this payment succeed" — it did not, and never will.
It is "has this order since been paid by some other attempt". Retrying without
asking debits somebody who has already paid, and that is a worse failure than
not recovering at all.

Verification is the shell's job, not the model's. The model may come to be what
*decides* a case is worth verifying; going and looking is code either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Verification:
    already_paid: bool
    detail: str
    checked: str


class Verifier(Protocol):
    """Somewhere the truth about an order can be obtained."""

    def order_settled(self, order_id: str) -> Verification: ...


class RazorpayVerifier:
    """Asks Razorpay whether anything on this order was captured."""

    def __init__(self, client) -> None:
        self._client = client

    def order_settled(self, order_id: str) -> Verification:
        payments = self._client.order.payments(order_id).get("items", [])
        settled = [p for p in payments if p.get("status") in ("captured", "authorized")]
        if settled:
            ids = ", ".join(p["id"] for p in settled)
            return Verification(
                already_paid=True,
                detail=f"order {order_id} was paid by {ids}",
                checked=f"{len(payments)} payment(s) on the order",
            )
        return Verification(
            already_paid=False,
            detail=f"order {order_id} has no captured or authorised payment",
            checked=f"{len(payments)} payment(s) on the order",
        )


class UnavailableVerifier:
    """Used where Razorpay cannot be reached.

    Reports that it could not tell, which is not the same as reporting that the
    order is unpaid. A caller must hold rather than proceed, because assuming
    "not paid" is exactly the assumption that double-debits somebody.
    """

    def order_settled(self, order_id: str) -> Verification:
        raise VerificationUnavailable(f"cannot verify order {order_id}")


class VerificationUnavailable(RuntimeError):
    """The check could not be performed. Never treat this as a negative result."""
