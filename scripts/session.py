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
import select
import sys

from unhalted import clock, config, tui
from unhalted.agent import handle_failure, handle_reply
from unhalted.core.scenarios import ERROR_SOURCE, METHOD, SCENARIOS
from unhalted.ingest.normalize import from_payment_failed
from unhalted.models import CaseState, FailureSignal
from unhalted.runner import run_due
from unhalted.shell import ladder, windows
from unhalted.store import Store

ROOT = pathlib.Path(__file__).parent.parent
CAPTURED = ROOT / "tests" / "fixtures" / "razorpay" / "captured"
MERCHANT = "Acme Streaming"
#: Matches `scripts/inject.py` — real captured payments never reach a contact
#: rung (issue #8), so this is the only way this terminal's reply loop is
#: ever reachable at all.
AMOUNT_PAISE = 49900
#: The same database the CLI reads. One store, three ways in: this terminal,
#: `scripts/review.py`, and `unhalted case`. Overridable with UNHALTED_DB.
SESSION_DB = pathlib.Path(config.database_path())
#: How often the reply loop checks for a recovery while waiting on input.
RECOVERY_POLL_SECONDS = 1.0

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


def scenario_signal(reason: str, *, now) -> FailureSignal:
    """The same construction `scripts/inject.py` uses, not a second version of
    it — deterministic `payment_id` per reason, so running the same scenario
    twice matches back to the same case rather than duplicating it.

    Exists because every real captured payment on this account diagnoses
    `recoverable-technical` (issue #8), which never contacts the customer —
    so a real payment can never reach this script's reply loop at all. This
    is the only way to actually exercise it against a genuine `NOTIFICATION_GAP`
    or beyond, the same as `unhalted.core.scenarios` already lets
    `scripts/inject.py` do without a conversation attached.
    """
    known = dict(SCENARIOS)
    if reason not in known:
        sys.exit(f"not a known scenario: {reason!r}. See scripts/inject.py --list.")
    return FailureSignal(
        payment_id=f"pay_INJECTED_{reason}",
        customer_ref=f"cust_injected_{reason}",
        amount_paise=AMOUNT_PAISE,
        occurred_at=now,
        source="inject",
        method=METHOD,
        error_reason=reason,
        error_source=ERROR_SOURCE,
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
    ap.add_argument(
        "--scenario",
        metavar="REASON",
        help="use an injected scenario (see `scripts/inject.py --list`) instead of a real "
             "captured payment. The only way to reach a rung this account's real payments "
             "never do (issue #8) — and so the only way to reach this script's reply loop.",
    )
    args = ap.parse_args()
    try:
        stated, note = clock.resolve(args.at)
    except clock.BadTime as exc:
        sys.exit(str(exc))

    source_desc = "an injected scenario" if args.scenario else "a real captured Razorpay failure"
    print(tui.banner(
        "CUSTOMER — the recovery conversation",
        f"{MERCHANT} · {source_desc} · type a reply, Ctrl-D to finish",
    ))

    store = Store(str(SESSION_DB))
    now = windows.as_ist(stated)
    if note:
        print(note)
    today = now.date()

    signal = scenario_signal(args.scenario, now=now) if args.scenario else real_signal(store)

    rule("1. A payment failure arrives")
    print(f"  payment  {signal.payment_id}   Rs {signal.amount_rupees:.0f}   {signal.method}")
    print(f"  Razorpay says: reason={signal.error_reason}  source={signal.error_source}  "
          f"step={signal.error_step}")
    if args.scenario:
        print(
            f"  {DIM}built locally from a documented Razorpay test-card scenario, not a real "
            f"webhook — see scripts/inject.py's docstring{RESET}"
        )
    else:
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

    # Known before step 3 runs, from the diagnosis alone — used to title that
    # step honestly (a message arriving is not the same event as a retry
    # nobody sees) and, after, to decide whether step 4 has anything to
    # reply to at all.
    rung = ladder.entry_rung(diagnosis.klass)
    contacted = rung is not None and ladder.LADDER[rung].is_contact

    rule("3. The message reaches the customer" if contacted else
         "3. Whatever the ladder scheduled actually runs")
    # Not a hand-picked nudge: this is the same `run_due` that a real deployment's
    # scheduler or `/internal/run-due` calls. Whatever `handle_failure` actually
    # scheduled above — a nudge, a retry, a reauthorisation with no executor yet,
    # or nothing at all if the case sits at SILENT_RETRY with nothing due this
    # instant — is what executes here, contact hours and all. A hardcoded nudge
    # regardless of diagnosis was a script pretending to be the pipeline; this is
    # the pipeline. On a contact rung, the boxed text above the report below is
    # the actual message — printed by the same `ConsoleNotifier` a real channel
    # adapter would stand in for, not reconstructed for display here.
    report = run_due(store, now=now)
    print(f"  {report.render()}")

    # `contacted` is about the diagnosis; this is about what actually
    # happened. A contact rung deferred by contact hours (the same
    # 08:00-19:00 window this system enforces everywhere else) sent nothing
    # at all — checked directly against the audit trail's own record of the
    # execution, not assumed from the rung alone.
    execution = next(
        (r for r in reversed(store.timeline(case.id)) if r.decision_type == "execution"),
        None,
    )
    ever_delivered = contacted and execution is not None and execution.action.endswith(": done")
    # A separate question from `ever_delivered`: `session.py` is idempotent
    # (deterministic payment_id, matched back to the same case), so a second
    # run against an already-contacted case reports claimed=0, prints no box
    # at all, and `execution` above is still the *earlier* run's record. Only
    # `execution.at == now` proves this exact pass is what produced it — a
    # `record.at` equal to `now` is possible only because `runner._record`
    # writes the very `now` this call passed in, verbatim.
    delivered_this_pass = ever_delivered and execution.at == now

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
    elif not ever_delivered:
        rule("4. Nothing has reached the customer yet")
        detail = execution.outcome if execution else "nothing executed this pass"
        print(f"  {DIM}{detail} — there is nothing yet for a reply loop to answer.{RESET}")
        print(
            f"  {DIM}rerun with `--at \"YYYY-MM-DD 08:00\"` (inside 08:00-19:00 IST) to see it "
            f"actually deliver and reach the reply loop.{RESET}"
        )
    else:
        rule("4. Your turn — reply as the customer")
        if delivered_this_pass:
            print(
                f"  {DIM}That boxed text above is what just arrived on your phone. Type your "
                f"reply to it and press enter, Ctrl-D to finish — or just pay the link: this "
                f"ends on its own the moment the webhook confirms it.{RESET}"
            )
        else:
            print(
                f"  {DIM}This case was already contacted in an earlier run — {case.id} matched "
                f"back to it, so nothing new printed above this time. Reply as if answering "
                f"that earlier message, Ctrl-D to finish — or pay the link it carried.{RESET}"
            )

        while True:
            # Checked before every read, not just after one: the webhook that
            # closes this case arrives in a separate process (the server, or
            # `scripts/schedule.py --run`), so nothing about typing a reply is
            # what notices a payment landed. `select` lets this wait on stdin
            # without blocking past a payment that arrives while nobody types
            # anything at all — a plain `for line in sys.stdin` cannot do that.
            if store.get_case(case.id).state is CaseState.RECOVERED:
                print(f"\n  {DIM}agent{RESET}  payment received — this case is settled.")
                break

            ready, _, _ = select.select([sys.stdin], [], [], RECOVERY_POLL_SECONDS)
            if not ready:
                continue

            line = sys.stdin.readline()
            if line == "":  # Ctrl-D
                break
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
