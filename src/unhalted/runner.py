"""What actually makes a scheduled action happen.

Until this existed, `pending_actions` held rows describing intentions nobody
acted on. A retry scheduled for 13:00 was a record that somebody meant to charge
at 13:00, and 13:00 arrived and nothing occurred. Three writers, no runner.

The queue is the table
----------------------
No broker. A row with a due time and a state is a durable queue already, and it
is the same transaction boundary the audit trail and the stop rules use — so a
revocation cancelling a retry and the retry itself cannot interleave wrongly.
Adding a broker would put that consistency across a network.

Leasing, not locking
--------------------
A worker claims due rows by setting `leased_until` in the same transaction that
reads them. Claim-then-read is one statement pair inside one transaction because
selecting first and updating after is how the same retry gets handed to two
workers; this repository has already shipped one check-then-act race and does not
need a second.

A lease expires. A worker that dies mid-action does not strand its rows — they
return to `pending` on the next pass and are tried again. That makes delivery
**at-least-once**, never exactly-once, and every executor below has to be safe
under repetition. It is the same discipline the ingest side already applies to
Razorpay's webhook redelivery.

Cancellation wins
-----------------
State is re-read immediately before the action fires. A customer revoking while
a worker holds the lease must still stop the charge, so `cancel_pending` reaches
leased rows and the runner checks for that before acting rather than after.

Where the work is absent
------------------------
`nudge` executes for real, through the same notifier and the same contact-hour
gate the rest of the system uses. `retry` does not: initiating a debit needs a
live mandate token, and this account cannot register one for UPI at all. Rather
than pretend, the runner refuses an action it has no adapter for, records why,
and routes the case to a person. An absent capability that says so is honest; a
stub that returns success is not.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from unhalted.models import AuditRecord, CaseState
from unhalted.shell import windows
from unhalted.shell.notify import ConsoleNotifier, Message, Notifier, deliver
from unhalted.store import Store

log = logging.getLogger("unhalted.runner")

#: How long a worker may hold an action before it is assumed dead. Long enough
#: that a slow HTTP call is not treated as a crash, short enough that a real
#: crash is recovered within one cycle.
LEASE = timedelta(minutes=5)

#: Actions claimed per pass. Bounded so one worker cannot take the whole queue
#: and then die holding it.
BATCH = 50


@dataclass(frozen=True)
class Outcome:
    """What an executor did with one action."""

    state: str
    detail: str
    #: Set when the action should be tried again rather than closed out.
    retry_at: datetime | None = None
    #: Set when a person has to take it from here.
    hold_case: bool = False


@dataclass
class RunReport:
    """What one pass of the runner did. Printed, logged and asserted on."""

    at: datetime
    worker: str
    reclaimed: int = 0
    claimed: int = 0
    done: int = 0
    held: int = 0
    cancelled: int = 0
    no_adapter: int = 0
    failed: int = 0
    lines: list[str] = field(default_factory=list)

    def render(self) -> str:
        head = (
            f"{self.at:%Y-%m-%d %H:%M %Z}  worker={self.worker}  "
            f"claimed={self.claimed}  done={self.done}  held={self.held}  "
            f"cancelled={self.cancelled}  no-adapter={self.no_adapter}  "
            f"failed={self.failed}"
        )
        if self.reclaimed:
            head += f"  reclaimed={self.reclaimed}"
        return "\n".join([head, *self.lines]) if self.lines else head


Executor = Callable[[Store, dict[str, Any], datetime], Outcome]


def worker_name() -> str:
    """Identifies who holds a lease, so a stuck one can be traced to a process."""
    return f"{socket.gethostname()}:{os.getpid()}"


# -- executors ---------------------------------------------------------------


def execute_nudge(store: Store, action: dict[str, Any], now: datetime) -> Outcome:
    """Send the message this action was scheduled for.

    Real. It goes through `deliver`, so the contact-hour rule applies here
    exactly as it does everywhere else — an action becoming due at 02:00 is not
    a reason to message somebody at 02:00.
    """
    case = store.get_case(action["case_id"])
    if case is None:
        return Outcome("failed", "the case this action belongs to is gone")

    notifier: Notifier = ConsoleNotifier()
    message = Message(
        customer_ref=case.customer_ref,
        body=(
            f"Your payment of Rs {case.amount_rupees:.0f} didn't go through. "
            f"Reply here if that doesn't suit. Reply STOP to opt out."
        ),
        case_id=case.id,
    )
    result = deliver(notifier, message, now=now)
    if result.sent:
        return Outcome("done", "message delivered")

    # Refused on contact hours rather than failed. It is not an error, and the
    # action is owed a later attempt rather than a failure record.
    return Outcome(
        "pending",
        f"not sent: {result.reason}",
        retry_at=windows.next_allowed_contact(now),
    )


def execute_retry(store: Store, action: dict[str, Any], now: datetime) -> Outcome:
    """Initiate the debit this action was scheduled for.

    **No adapter exists on this deployment.** A debit needs a live mandate
    token; UPI Autopay cannot be enabled on this account at all, and the card
    and emandate mandates that can be registered require a human at a checkout.

    So this refuses, loudly and in the audit trail, rather than reporting a
    success nobody performed. `unhalted capabilities` says the same thing from
    the other direction.
    """
    return Outcome(
        "no-adapter",
        "this deployment cannot initiate a debit: no live mandate token. "
        "The decision stands and is recorded; the execution is absent",
        hold_case=True,
    )


#: Which executor handles which kind of scheduled action.
EXECUTORS: dict[str, Executor] = {
    "nudge": execute_nudge,
    "retry": execute_retry,
}


# -- the loop ----------------------------------------------------------------


def run_due(
    store: Store,
    *,
    now: datetime | None = None,
    executors: dict[str, Executor] | None = None,
    worker: str | None = None,
    limit: int = BATCH,
) -> RunReport:
    """One pass: reclaim, claim, execute, record.

    Idempotent by design. Running it twice in a second does no harm — the second
    pass finds nothing due — so it is safe on a timer, on an HTTP request, or by
    hand, which is what lets the deployment shape stay an open question.
    """
    now = windows.as_ist(now or datetime.now(tz=windows.IST))
    who = worker or worker_name()
    # `is None`, not falsy: an empty mapping means this deployment registers
    # no executors, which is a real configuration and not a request for the
    # defaults. `or` silently turned one into the other.
    table = EXECUTORS if executors is None else executors
    report = RunReport(at=now, worker=who)

    report.reclaimed = store.release_expired_leases(now)
    if report.reclaimed:
        log.warning("reclaimed %d action(s) from a worker that did not finish",
                    report.reclaimed)

    claimed = store.lease_due_actions(now, worker=who, lease_for=LEASE, limit=limit)
    report.claimed = len(claimed)

    for action in claimed:
        action_id = int(action["id"])
        kind = action["kind"]

        # Re-read before acting. A stop rule may have cancelled this action
        # between the claim and now, and a cancellation must win.
        current = store.action(action_id)
        if current is None or current["state"] == "cancelled":
            report.cancelled += 1
            report.lines.append(f"  #{action_id} {kind}: cancelled before it ran")
            continue

        executor = table.get(kind)
        if executor is None:
            store.finish_action(action_id, state="no-adapter",
                                error=f"no executor registered for {kind!r}")
            report.no_adapter += 1
            report.lines.append(f"  #{action_id} {kind}: no executor registered")
            continue

        try:
            outcome = executor(store, action, now)
        except Exception as exc:
            # An executor blowing up must not take the pass down with it, or one
            # bad row stalls every other customer's recovery.
            log.exception("action %d (%s) raised", action_id, kind)
            store.finish_action(action_id, state="failed",
                                error=f"{type(exc).__name__}: {exc}")
            report.failed += 1
            report.lines.append(f"  #{action_id} {kind}: raised {type(exc).__name__}")
            continue

        _record(store, action, outcome, now, who)

        if outcome.state == "pending" and outcome.retry_at is not None:
            store.return_action(action_id, scheduled_for=outcome.retry_at)
            report.lines.append(
                f"  #{action_id} {kind}: {outcome.detail}, moved to "
                f"{outcome.retry_at:%H:%M}"
            )
            continue

        store.finish_action(action_id, state=outcome.state, error=None
                            if outcome.state == "done" else outcome.detail)

        if outcome.hold_case:
            store.set_state(action["case_id"], CaseState.HELD_FOR_HUMAN)
            report.held += 1
        elif outcome.state == "done":
            report.done += 1
        elif outcome.state == "no-adapter":
            report.no_adapter += 1
        else:
            report.failed += 1

        report.lines.append(f"  #{action_id} {kind}: {outcome.detail}")

    return report


def _record(
    store: Store,
    action: dict[str, Any],
    outcome: Outcome,
    now: datetime,
    who: str,
) -> None:
    """Put the execution in the audit trail, beside the decision that caused it.

    A decision recorded without its execution is half an account. This is the
    other half, and it carries the worker's name for the same reason a human
    review carries the reviewer's.
    """
    store.record(
        AuditRecord(
            case_id=action["case_id"],
            at=now,
            decision_type="execution",
            action=f"{action['kind']}: {outcome.state}",
            inputs={
                "action_id": action["id"],
                "scheduled_for": action["scheduled_for"],
                "attempt": action["attempts"],
                "worker": who,
            },
            rules_fired=["RUNNER"],
            outcome=outcome.detail,
        )
    )
