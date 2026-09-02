"""Sending a message to a customer.

The channel is transport; the gating is policy. Contact hours, the weekly
ceiling and the compliance lint all sit *above* this, so they apply identically
whether a message goes to WhatsApp or to a terminal. That is the point of the
seam: swapping the transport must not be able to swap the rules.

`ConsoleNotifier` is not a simulation of sending. It is a real delivery to a
real destination that happens to be a terminal, and the message it carries is
the same one WhatsApp would receive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from unhalted.shell import windows


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
