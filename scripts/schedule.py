"""The scheduler's terminal: what is queued, what comes due, what happened.

    uv run python scripts/schedule.py          # watch
    uv run python scripts/schedule.py --run    # watch, and be the worker

A retry in this system is not a line in a log. It is a row a worker claims under
a lease, and this is the view of that: every action as it is scheduled, comes
due, executes, is deferred, or is cancelled by a stop rule.

Why a log and not a table
-------------------------
A redrawing table shows state; a log shows *events*, and the argument this view
exists to make is about sequence — a charge was scheduled, then the customer
said stop, then the charge did not happen. Append-only means a viewer can read
back up the screen and see the order for themselves rather than being told it.

Watching versus working
-----------------------
Without `--run` this observes: it reads the store and reports. With `--run` it
is also the worker — the same `run_due` the CLI and the HTTP endpoint call — so
an action that comes due while you are watching actually executes.

Both are real. Neither invents an event: every line comes from a row or an audit
record that something else wrote.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
from datetime import datetime
from typing import Any

from unhalted import clock, config, tui
from unhalted.runner import run_due
from unhalted.shell import windows
from unhalted.store import Store

SESSION_DB = pathlib.Path(config.database_path())

#: Fast enough to feel live on camera, slow enough not to hammer the file.
POLL_SECONDS = 1.5

#: How each event reads at a glance. The colour carries the meaning so a viewer
#: does not have to parse the word first.
STYLE = {
    "SCHEDULED": tui.BLUE,
    "DUE": tui.AMBER,
    "EXECUTED": tui.GREEN,
    "DEFERRED": tui.AMBER,
    "CANCELLED": tui.RED,
    "HELD": tui.RED,
    "NO ADAPTER": tui.RED,
    "FAILED": tui.RED,
    "RECLAIMED": tui.AMBER,
    "REVIEWED": tui.VIOLET,
    "RECOVERED": tui.GREEN,
    "ESCALATED": tui.RED,
}

#: Cancellation reasons that mean "this case now needs a person" — every stop
#: rule and diagnosis path whose terminal state is CaseState.HELD_FOR_HUMAN.
#: Rendered as a distinct event so `CANCELLED ... HELD_FOR_HUMAN` doesn't read
#: as a routine cancellation on a log a person is trying to follow live.
ESCALATION_REASONS = frozenset({"HELD_FOR_HUMAN", "DISPUTE", "DISTRESS", "CHARGEBACK", "REG_HOLD"})


def cancellation_event(action: dict[str, Any], *, now: datetime) -> str | None:
    """One cancelled action, or `None` for the one reason that already gets
    its own distinct event elsewhere (RECOVERED, via `audit_lines`) and would
    otherwise print twice for the same real thing that happened.

    An escalation reason renders as ESCALATED, not CANCELLED — mechanically
    it is a cancellation, `apply_stop` and the low-confidence-diagnosis path
    both reach it through `store.cancel_pending`, but to a person reading the
    log live "the retry got cancelled" and "this case now needs you" are not
    the same sentence, and only one of the two reasons to write.
    """
    reason = action["cancel_reason"] or ""
    if reason == "RECOVERED":
        return None
    if reason in ESCALATION_REASONS:
        return event("ESCALATED", action["case_id"],
                     f"{action['kind']}  held for a human — {reason}", now=now)
    return event("CANCELLED", action["case_id"], f"{action['kind']}  {reason}", now=now)


def event(kind: str, case_id: str, detail: str, *, now: datetime) -> str:
    tint = STYLE.get(kind, tui.BLUE)
    return (
        f"  {tui.paint(f'{now:%H:%M:%S}', tui.DIM)}  "
        f"{tui.pad(tui.chip(kind, tint), 22)} "
        f"{tui.paint(case_id, tui.DIM)}  {detail}"
    )


def describe(action: dict[str, Any], now: datetime) -> str:
    when = action.get("scheduled_for")
    at = datetime.fromisoformat(when) if when else None
    kind = action["kind"]
    if at is None:
        return f"{kind}  unscheduled"
    return (
        f"{tui.pad(kind, 10)} {tui.clock(at)}  "
        f"{tui.paint(tui.relative(at, now), tui.DIM)}"
    )


def audit_lines(
    store: Store, seen: set[tuple[str, str, str]], *, now: datetime,
) -> list[str]:
    """Executions and human decisions, both read from the audit trail rather
    than from the runner's own report — so a pass run by another worker, the
    HTTP endpoint, or a reviewer in a different terminal all show up here too.

    Without the human-review half of this, approving, rejecting or
    reclassifying a held case was invisible from here: the log stopped at the
    cancellation that sent a case to a person and never said what the person
    then did about it. Recovery — a customer actually paying through the
    recovery link — is the same shape of gap: the case closes in the store,
    and nothing here said so until this branch existed.
    """
    lines: list[str] = []
    for case in store.all_cases():
        for record in store.timeline(case.id):
            if record.decision_type not in ("execution", "human-review", "recovery"):
                continue
            marker = (record.case_id, record.at.isoformat(), record.action)
            if marker in seen:
                continue
            seen.add(marker)
            if record.decision_type == "human-review":
                who = record.human_actor or "a reviewer"
                lines.append(event("REVIEWED", case.id, f"{record.action}, by {who}", now=now))
                continue
            if record.decision_type == "recovery":
                lines.append(event("RECOVERED", case.id, record.action, now=now))
                continue
            state = record.action.split(":")[-1].strip().upper()
            kind = {
                "DONE": "EXECUTED",
                "NO-ADAPTER": "NO ADAPTER",
                "PENDING": "DEFERRED",
            }.get(state, state)
            lines.append(event(kind, case.id, record.outcome or record.action, now=now))
    return lines


def header(db: pathlib.Path, running: bool, note: str | None) -> None:
    print(tui.clear(), end="")
    mode = (
        tui.chip("WORKER", tui.GREEN) + " executing what comes due"
        if running
        else tui.chip("WATCHING", tui.BLUE) + " observing only, not executing"
    )
    print(tui.banner(
        "SCHEDULER — the durable queue",
        f"{db.name} · a lease, not a note in a log · Ctrl-C to close",
    ))
    print()
    print(f"  {mode}")
    if note:
        print(note)
    print(tui.rule())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true",
                    help="also execute actions that come due, rather than only watching")
    ap.add_argument("--at", metavar="'YYYY-MM-DD HH:MM'",
                    help="evaluate due-ness against this time (IST). Announces itself.")
    args = ap.parse_args()

    try:
        stated, note = clock.resolve(args.at)
    except clock.BadTime as exc:
        sys.exit(str(exc))

    if not SESSION_DB.exists():
        print(f"no database at {SESSION_DB.name}. Start the customer terminal first:")
        print("  uv run python scripts/session.py")
        return 1

    store = Store(str(SESSION_DB))
    header(SESSION_DB, args.run, note)

    # What has already been reported, so a poll shows only what changed. Keyed
    # by action id and state, because an action legitimately appears more than
    # once as it moves from scheduled to due to executed.
    reported: set[tuple[int, str]] = set()
    # An audit record carries no id, so it is identified by what makes it
    # unique in practice: the case, the instant, and the action.
    seen_audit: set[tuple[str, str, str]] = set()
    idle_ticks = 0

    try:
        while True:
            now = stated if args.at else windows.as_ist(datetime.now(tz=windows.IST))
            lines: list[str] = []

            # Cancellations first. Within one poll a stop rule cancels and then
            # reschedules, and printing the new row above the cancelled ones
            # showed a viewer the effect before the cause.
            for action in store.actions(state="cancelled"):
                key = (int(action["id"]), "cancelled")
                if key in reported:
                    continue
                reported.add(key)
                line = cancellation_event(action, now=now)
                if line is not None:
                    lines.append(line)

            for action in store.pending_actions():
                key = (int(action["id"]), "scheduled")
                if key in reported:
                    continue
                reported.add(key)
                lines.append(event("SCHEDULED", action["case_id"],
                                   describe(action, now), now=now))

            # Due but not yet executed, so a viewer sees the moment arrive even
            # when nothing is running the work.
            for action in store.pending_actions():
                when = action.get("scheduled_for")
                if not when:
                    continue
                if datetime.fromisoformat(when) > now:
                    continue
                key = (int(action["id"]), "due")
                if key in reported:
                    continue
                reported.add(key)
                lines.append(event("DUE", action["case_id"],
                                   f"{action['kind']} is ready to run", now=now))

            if args.run:
                report = run_due(store, now=now)
                if report.reclaimed:
                    lines.append(event("RECLAIMED", "—",
                                       f"{report.reclaimed} action(s) from a worker that "
                                       f"did not finish", now=now))

            lines.extend(audit_lines(store, seen_audit, now=now))

            if lines:
                idle_ticks = 0
                print("\n".join(lines), flush=True)
            else:
                idle_ticks += 1
                if idle_ticks % 20 == 0:
                    print(tui.paint(f"  {now:%H:%M:%S}  nothing due", tui.DIM), flush=True)

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print(tui.paint("\n  scheduler closed.\n", tui.DIM))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
