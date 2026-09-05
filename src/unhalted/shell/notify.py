"""Sending a message to a customer.

The channel is transport; the gating is policy. Contact hours are checked in
`deliver` below, and the contact ceiling in the runner that calls it —
both *above* the notifier, so they apply identically whether a message goes to
WhatsApp or to a terminal. That is the point of the seam: swapping the
transport must not be able to swap the rules.

This docstring claimed a weekly ceiling for some time before anything counted
contacts. It is named here now because `windows.contact_budget` exists and
`runner.execute_nudge` asks it before every send.

`ConsoleNotifier` is not a simulation of sending. It is a real delivery to a
real destination that happens to be a terminal, and the message it carries is
the same one WhatsApp would receive.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from unhalted.shell import windows


class NudgeVariant(str, enum.Enum):
    """Which message a nudge carries.

    The rung and the executor are the same for all three; only the words
    differ, and they differ because the *situation* differs. Sending the
    first-touch message to somebody who just asked for a specific retry
    date reads as not having listened, which is worse than not writing.
    """

    #: We have not reached this customer yet about this failure.
    STANDARD = "standard"
    #: An empty account: the one failure whose fix depends on a date only
    #: the customer knows. Ask, rather than guessing three times.
    ASK_DATE = "ask-date"
    #: The retries this cycle allows are spent. A payable link is what is
    #: left, and saying why is the difference between honest and confusing.
    EXHAUSTED = "exhausted"


def nudge_body(
    amount_rupees: float, *, merchant: str = "", when: str = "", pay_link: str | None = None,
) -> str:
    """The plain, factual message a nudge carries. Shared by the customer
    terminal and the real runner so the two do not silently drift into two
    different messages for the same event — C7 drafts and lints a warmer
    version of this; this is what goes out when that path has nothing, or has
    not been reached at all.

    `pay_link` is a real, payable link when one was generated (see
    `shell.paylink`) — the ladder prices this rung as the answer for someone
    who would rather pay from a different account than wait on a retry of the
    one that just failed. Its absence is not an error: a nudge is not worth
    holding over a link that failed to generate.
    """
    who = f"{merchant} " if merchant else ""
    lines = [f"Hi — your {who}payment of Rs {amount_rupees:.0f} didn't go through."]
    if when:
        lines.append(f"We'll try again on {when}.")
    if pay_link:
        lines.append(f"Prefer to pay another way? {pay_link}")
    else:
        lines.append("Reply here if that doesn't suit.")
    lines.append("Reply STOP to opt out of these messages.")
    return "\n".join(lines)


def ask_date_body(amount_rupees: float, *, merchant: str = "") -> str:
    """Ask when to try again, rather than guessing three times.

    `core/reply.py` states the reason this message exists: for the largest
    failure class — an empty account — whether a retry works depends on
    when the customer will have money, and no API anywhere reports that.
    Three blind attempts on a fixed schedule spend NPCI's whole allowance
    guessing at a fact one question would have established.
    """
    who = f"{merchant} " if merchant else ""
    opening = (
        f"Hi — your {who}payment of Rs {amount_rupees:.0f} didn't go through: "
        "there wasn't enough balance in the account."
    )
    lines = [opening]
    lines.append("When would be a good time to try again? Reply with a date and we'll use it.")
    lines.append("Reply STOP to opt out of these messages.")
    return "\n".join(lines)


def exhausted_body(
    amount_rupees: float, *, merchant: str = "", pay_link: str | None = None,
) -> str:
    """Say the automatic attempts are spent, and give a way to pay anyway.

    Reached both when the retries ran out on their own and when a customer
    named a date the cap can no longer honour. Either way the honest thing
    is to say why this is arriving rather than reuse the first-touch text,
    which would read as never having listened.
    """
    who = f"{merchant} " if merchant else ""
    opening = (
        f"Hi — we tried a few times but couldn't collect your {who}payment of "
        f"Rs {amount_rupees:.0f} automatically."
    )
    lines = [opening]
    if pay_link:
        lines.append(f"You can pay it directly here, whenever suits: {pay_link}")
    else:
        lines.append("Reply here and we'll send you a link to pay directly.")
    lines.append("Reply STOP to opt out of these messages.")
    return "\n".join(lines)


def body_for(
    variant: NudgeVariant | str | None,
    amount_rupees: float,
    *,
    merchant: str = "",
    when: str = "",
    pay_link: str | None = None,
) -> str:
    """The message for a nudge, chosen by the variant the decision recorded.

    An unknown or missing variant is the standard first-touch message: an
    action scheduled before variants existed still has to send something,
    and the first-touch wording is the one that assumes least.
    """
    match variant:
        case NudgeVariant.ASK_DATE | NudgeVariant.ASK_DATE.value:
            return ask_date_body(amount_rupees, merchant=merchant)
        case NudgeVariant.EXHAUSTED | NudgeVariant.EXHAUSTED.value:
            return exhausted_body(amount_rupees, merchant=merchant, pay_link=pay_link)
        case _:
            return nudge_body(amount_rupees, merchant=merchant, when=when, pay_link=pay_link)


@dataclass(frozen=True)
class Message:
    """What gets sent, and to whom."""

    customer_ref: str
    body: str
    case_id: str
    kind: str = "nudge"


@dataclass(frozen=True)
class Delivery:
    sent: bool
    channel: str
    reason: str
    deferred_to: datetime | None = None


class Notifier(Protocol):
    """Any channel a customer can be reached on."""

    channel: str

    def send(self, message: Message) -> Delivery: ...


class ConsoleNotifier:
    """Delivers to a terminal. Used where a person is watching rather than a phone."""

    channel = "console"

    def __init__(self, stream=None) -> None:
        import sys

        self.stream = stream or sys.stdout
        self.sent: list[Message] = []

    def send(self, message: Message) -> Delivery:
        self.sent.append(message)
        print(f"\n  ┌─ to {message.customer_ref} ({message.kind})", file=self.stream)
        for line in message.body.splitlines():
            print(f"  │ {line}", file=self.stream)
        print("  └─", file=self.stream)
        return Delivery(sent=True, channel=self.channel, reason="delivered to console")


def deliver(
    notifier: Notifier,
    message: Message,
    *,
    now: datetime,
) -> Delivery:
    """Send, unless the hour forbids it.

    Contact hours apply to every channel and to every kind of message,
    including a retry of one that failed to send. The specification's case is a
    send failing at 18:58 whose retry would fire at 19:05: that is deferred to
    08:00, not slipped through because it was already in flight.
    """
    check = windows.is_contact_allowed(now)
    if not check.allowed:
        deferred = windows.next_allowed_contact(now)
        return Delivery(
            sent=False,
            channel=notifier.channel,
            reason=f"{check.reason}; deferred to {deferred:%Y-%m-%d %H:%M %Z}",
            deferred_to=deferred,
        )
    return notifier.send(message)
