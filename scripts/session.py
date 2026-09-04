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

from unhalted import clock, config, tui
from unhalted.agent import handle_failure, handle_reply
from unhalted.ingest.normalize import from_payment_failed
from unhalted.models import CaseState
from unhalted.runner import run_due
from unhalted.shell import ladder, windows
from unhalted.store import Store

ROOT = pathlib.Path(__file__).parent.parent
CAPTURED = ROOT / "tests" / "fixtures" / "razorpay" / "captured"
MERCHANT = "Acme Streaming"
#: The same database the CLI reads. One store, three ways in: this terminal,
#: `scripts/review.py`, and `unhalted case`. Overridable with UNHALTED_DB.
SESSION_DB = pathlib.Path(config.database_path())

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def rule(title: str) -> None:
    print()
    print(tui.rule(title))


def real_signal(store: Store):
    """The next real captured failure this database hasn't seen yet.

    Always returning `files[0]` meant every run — even against a database
    already holding a case — replayed the same payment, so `open_case_or_get`
    correctly matched it back to the same case and it looked like the demo
    could only ever produce one. The three captured fixtures are three
    distinct real payments; this walks them in order and picks the first
    whose `payment_id` has no case yet, so running the script again gives you
    the next one rather than the same one back.
    """
    files = sorted(glob.glob(str(CAPTURED / "*.json")))
    if not files:
        sys.exit("no captured payments; see docs/capturing-fixtures.md")
    unseen = None
    for f in files:
        payment = json.loads(pathlib.Path(f).read_text())["payment"]
        if store.case_for_payment(payment["id"]) is None:
            unseen = payment
            break
    if unseen is None:
        payment = json.loads(pathlib.Path(files[-1]).read_text())["payment"]
        print(f"  {DIM}every captured payment already has a case in this database; "
              f"replaying {payment['id']}{RESET}")
    else:
        payment = unseen
    return from_payment_failed(
        {"event": "payment.failed", "payload": {"payment": {"entity": payment}},
         "created_at": payment.get("created_at")}
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

    print(tui.banner(
        "CUSTOMER — the recovery conversation",
        f"{MERCHANT} · a real captured Razorpay failure · type a reply, Ctrl-D to finish",
    ))

    store = Store(str(SESSION_DB))
    now = windows.as_ist(stated)
    if note:
        print(note)
    today = now.date()

    signal = real_signal(store)

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

    rule("3. Whatever the ladder scheduled actually runs")
    # Not a hand-picked nudge: this is the same `run_due` that a real deployment's
    # scheduler or `/internal/run-due` calls. Whatever `handle_failure` actually
    # scheduled above — a nudge, a retry, a reauthorisation with no executor yet,
    # or nothing at all if the case sits at SILENT_RETRY with nothing due this
    # instant — is what executes here, contact hours and all. A hardcoded nudge
    # regardless of diagnosis was a script pretending to be the pipeline; this is
    # the pipeline.
    report = run_due(store, now=now)
    print(f"  {report.render()}")

    # A reply is an answer to a message. `recoverable-technical` and
    # `recoverable-balance` enter the ladder at SILENT_RETRY, which by design
    # never contacts the customer — there is nothing here for them to be
    # replying to. Prompting for one anyway asked you to answer a message
    # this case never sent, the same shortcut step 3 used to take.
    rung = ladder.entry_rung(diagnosis.klass)
    contacted = rung is not None and ladder.LADDER[rung].is_contact

    if not contacted:
        rule("4. Nobody was contacted, so there is nothing to reply to")
        print(
            f"  {DIM}{diagnosis.klass.value} enters the ladder at "
            f"'{ladder.LADDER[rung].name if rung else 'no rung'}' — no message goes to the "
            f"customer for this diagnosis, so a reply loop here would be answering something "
            f"never sent.{RESET}"
        )
        print(
            f"  {DIM}see the reply loop with `uv run python scripts/inject.py "
            f"authentication_failed` instead — the real captured payments only ever produce "
            f"this diagnosis (issue #8).{RESET}"
        )
    else:
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
                "  ".join(f"{i.type.value}({i.confidence})" for i in parsed.intents)
                or "nothing read"
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
