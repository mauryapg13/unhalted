"""Drive one case end to end, with a person playing the customer.

Everything here is the real pipeline: a real captured Razorpay payment enters
through the real normaliser, is diagnosed by the taxonomy generated from
Razorpay's documentation, scheduled by the shell that enforces NPCI's windows,
and the reply you type is read by the same parser the product uses.

The only substitution is the transport — messages arrive in this terminal
instead of on a phone. That is a channel, not a pretence: the gating above it
(contact hours, the ceilings, the stop rules) is identical either way.

    uv run python scripts/session.py

Message drafting is C7. The nudge below is plain and factual rather than
model-written, and says so.
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys

from unhalted import clock, config
from unhalted.agent import handle_failure, handle_reply
from unhalted.ingest.normalize import from_payment_failed
from unhalted.models import CaseState
from unhalted.shell import windows
from unhalted.shell.notify import ConsoleNotifier, Message, deliver
from unhalted.store import Store

ROOT = pathlib.Path(__file__).parent.parent
CAPTURED = ROOT / "tests" / "fixtures" / "razorpay" / "captured"
MERCHANT = "Acme Streaming"
#: The same database the CLI reads. One store, three ways in: this terminal,
#: `scripts/review.py`, and `unhalted case`. Overridable with UNHALTED_DB.
SESSION_DB = pathlib.Path(config.database_path())

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def rule(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}\n{DIM}{'─' * 66}{RESET}")


def real_signal():
    files = sorted(glob.glob(str(CAPTURED / "*.json")))
    if not files:
        sys.exit("no captured payments; see docs/capturing-fixtures.md")
    payment = json.loads(pathlib.Path(files[0]).read_text())["payment"]
    return from_payment_failed(
        {"event": "payment.failed", "payload": {"payment": {"entity": payment}},
         "created_at": payment.get("created_at")}
    )


def nudge_body(amount_rupees: float, when: str) -> str:
    """A plain factual message. C7 replaces this with drafted, linted copy."""
    return (
        f"Hi — your {MERCHANT} payment of Rs {amount_rupees:.0f} didn't go through.\n"
        f"We'll try again on {when}. Reply here if that doesn't suit.\n"
        f"Reply STOP to opt out of these messages."
    )


def main() -> int:
    # A file, not memory, so `scripts/review.py` in another terminal sees the
    # same cases. The human queue is not a view onto a copy.
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--at",
        metavar="'YYYY-MM-DD HH:MM'",
        help="rehearse against this time (IST) rather than now. Announces itself.",
    )
    args = ap.parse_args()
    try:
        stated, note = clock.resolve(args.at)
    except clock.BadTime as exc:
        sys.exit(str(exc))

    store = Store(str(SESSION_DB))
    notifier = ConsoleNotifier()
    now = windows.as_ist(stated)
    if note:
        print(note)
    today = now.date()

    signal = real_signal()

    rule("1. A real payment failure arrives")
    print(f"  payment  {signal.payment_id}   Rs {signal.amount_rupees:.0f}   {signal.method}")
    print(f"  Razorpay says: reason={signal.error_reason}  source={signal.error_source}  "
          f"step={signal.error_step}")
    print(f"  {DIM}captured from Razorpay test mode; see tests/fixtures/razorpay/PROVENANCE.md{RESET}")

    rule("2. The agent diagnoses and schedules")
    case = handle_failure(store, signal, now=now)
    diagnosis = store.latest_diagnosis(case.id)
    print(f"  {case.id}   state={case.state.value}")
    print(f"  {diagnosis.klass.value}  confidence {diagnosis.confidence}  "
          f"via {diagnosis.source.value}  ({diagnosis.authority})")
    print(f"  {DIM}{diagnosis.reasoning}{RESET}")
    for r in store.timeline(case.id):
        fired = ("  " + ", ".join(r.rules_fired)) if r.rules_fired else ""
        print(f"    {r.decision_type:<10} {r.action}{DIM}{fired}{RESET}")

    scheduled = next(
        (r for r in store.timeline(case.id) if r.decision_type == "schedule"), None
    )
    when = (
        scheduled.action.removeprefix("retry at ") if scheduled else "shortly"
    )

    rule("3. The agent contacts the customer")
    message = Message(
        customer_ref=signal.customer_ref,
        body=nudge_body(signal.amount_rupees, when),
        case_id=case.id,
    )
    store.schedule_action(case.id, signal.customer_ref, "nudge", now, now)
    delivery = deliver(notifier, message, now=now)
    if not delivery.sent:
        print(f"  {DIM}not sent: {delivery.reason}{RESET}")
        print(f"  {DIM}(contact hours are 08:00-19:00 IST and apply to every channel){RESET}")
        print(f"\n  Continuing anyway so you can test the reply loop.{RESET}")
        notifier.send(message)

    rule("4. Your turn — reply as the customer")
    print(f"  {DIM}Type a reply and press enter. Ctrl-D to finish.{RESET}")

    for line in sys.stdin:
        reply = line.strip()
        if not reply:
            continue

        parsed, outcome = handle_reply(
            store, case, reply,
            context=f"A Rs {signal.amount_rupees:.0f} {MERCHANT} renewal failed. "
                    f"Today is {today}.",
            now=now,
        )

        print(f"\n  {DIM}model{RESET}  " + (
            f"parse failed after {parsed.attempts} attempts — {parsed.failure_reason}"
            if parsed.failed else
            "  ".join(f"{i.type.value}({i.confidence})" for i in parsed.intents) or "nothing read"
        ))
        if parsed.payment_date_raw:
            print(f"         date proposed: {parsed.payment_date_raw}")

        print(f"  {DIM}shell{RESET}  " + "  ".join(outcome.rules_fired))
        for reason in outcome.reasons:
            print(f"         {DIM}{reason}{RESET}")

        state = store.get_case(case.id).state
        pending = store.pending_actions(case_id=case.id)
        print(f"  {DIM}agent{RESET}  case is {state.value}; "
              f"{len(pending)} action(s) still scheduled")
        if state is CaseState.HELD_FOR_HUMAN:
            print(f"         {DIM}a person picks this up from here{RESET}")

    rule("The case, as an auditor would read it")
    for r in store.timeline(case.id):
        fired = ("  [" + ", ".join(r.rules_fired) + "]") if r.rules_fired else ""
        print(f"  {r.at:%H:%M}  {r.decision_type:<10} {r.action}{DIM}{fired}{RESET}")
    print(f"\n  {DIM}held cases are reviewable in another terminal:{RESET}")
    print(f"  {DIM}  uv run python scripts/review.py{RESET}")
    print(f"  {DIM}  uv run unhalted case {case.id}{RESET}")
    print(f"  {DIM}  uv run unhalted compare {case.id}   <- against Razorpay's own policy{RESET}")
    print(f"  {DIM}state is in {SESSION_DB.name}{RESET}")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
