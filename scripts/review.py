"""The human review queue.

Cases reach here when the agent declines to act alone: a diagnosis it could not
make confidently, a reply it could not read, a dispute it cannot settle, a
cancellation it must not execute on the customer's behalf, or hardship that
should never be handled by automation at all.

    uv run python scripts/review.py

It stays open. A reviewer's terminal that exits the moment the queue is empty is
a reviewer's terminal that is never running when a case arrives, so this polls
and redraws, announces what appeared, and closes only when the reviewer says so.

Two things the specification insists on and this enforces:

- **Auto-approval by timeout is prohibited.** Nothing here expires into a yes.
  A case sits until a person decides.
- **Every decision is attributed.** The reviewer's name goes on the audit record,
  because a decision nobody is named for is a decision nobody is answerable for.

Message drafting and the model-written case summary are C7. What a reviewer sees
here is the raw material: the signals, the diagnosis and its reasoning, the
reply verbatim, and why the agent stopped.
"""

from __future__ import annotations

import os
import pathlib
import select
import sys
from datetime import datetime

from unhalted import config, tui
from unhalted.core.summarise import brief
from unhalted.models import AuditRecord, Case, CaseState, DiagnosisClass
from unhalted.shell import windows
from unhalted.store import Store

#: The same database the CLI and `scripts/session.py` use. A reviewer looking
#: at a different copy of the state is a reviewer looking at nothing.
SESSION_DB = pathlib.Path(config.database_path())

BOLD, DIM, RESET = tui.BOLD, tui.DIM, tui.RESET

#: How often to look for new work while nobody is typing. Fast enough that a
#: case appearing during a demo shows up while the moment is still live, slow
#: enough that the terminal is not redrawing under a reader's eyes.
POLL_SECONDS = 2.0


def reviewer() -> str:
    return os.environ.get("UNHALTED_REVIEWER") or os.environ.get("USER") or "unknown-reviewer"


def pick(held: list[Case], choice: str) -> Case | None:
    """Accept whatever the reviewer reasonably typed.

    The queue prints an id, so an id is the obvious thing to type. Demanding a
    row number instead is the tool making a person adapt to it.
    """
    if choice.isdigit():
        index = int(choice) - 1
        return held[index] if 0 <= index < len(held) else None

    wanted = choice.strip().upper().removeprefix("CASE-")
    matches = [c for c in held if c.id.upper().removeprefix("CASE-").startswith(wanted)]
    return matches[0] if len(matches) == 1 else None


def show_case(store: Store, case: Case) -> None:
    """The raw material. No model call — a reviewer must never wait on the
    model to be able to act, the same way a debit never waits on it either.
    """
    print(f"\n{BOLD}{case.id}{RESET}   Rs {case.amount_rupees:.0f}   "
          f"customer {case.customer_ref}   {DIM}state={case.state.value}{RESET}")

    for signal in store.signals(case.id):
        print(f"  {DIM}from Razorpay{RESET}  {signal.payment_id}  {signal.method}  "
              f"reason={signal.error_reason}  source={signal.error_source}")

    diagnosis = store.latest_diagnosis(case.id)
    if diagnosis:
        print(f"  {DIM}diagnosis{RESET}  {diagnosis.klass.value}  "
              f"confidence {diagnosis.confidence}  via {diagnosis.source.value}")
        print(f"    {DIM}{diagnosis.reasoning}{RESET}")

    print(f"  {DIM}why it stopped{RESET}")
    for record in store.timeline(case.id):
        if record.decision_type in ("reply", "stop"):
            fired = ", ".join(record.rules_fired)
            print(f"    {record.at:%H:%M}  {record.decision_type:<7} {record.action}"
                  f"{DIM}  [{fired}]{RESET}")
            if record.decision_type == "reply":
                print(f'      {DIM}customer said: "{record.inputs.get("reply", "")}"{RESET}')
            if record.outcome:
                print(f"      {DIM}{record.outcome}{RESET}")

    pending = store.pending_actions(case_id=case.id)
    print(f"  {DIM}pending automated actions: {len(pending)}{RESET}")


def show_briefing(store: Store, case: Case) -> None:
    """The model's read, fetched only when asked for.

    This is a live call — up to `summarise.TIMEOUT_SECONDS` — and the earlier
    version made every reviewer wait for it before they could even see the
    decision prompt. Nineteen silent seconds reads as a hang, not a wait, and a
    reviewer who did not know to expect it reasonably concluded the program was
    stuck. The fix is not a faster model; it is never blocking a decision the
    raw material already supports.
    """
    print(f"\n  {DIM}thinking — asking the model for its read on this case "
          f"(up to {60}s if the endpoint is slow)…{RESET}", flush=True)
    briefing = brief(_record_for_briefing(store, case))
    if briefing:
        print(f"  {BOLD}the agent's read{RESET} {DIM}(advice, not a finding){RESET}")
        for line in briefing.splitlines():
            if line.strip():
                print(f"    {line.strip()}")
    else:
        print(f"  {DIM}no briefing — the model was unavailable. The record above is complete.{RESET}")


def _record_for_briefing(store: Store, case: Case) -> str:
    parts = [f"Amount: Rs {case.amount_rupees:.0f}", f"State: {case.state.value}"]
    for signal in store.signals(case.id):
        parts.append(
            f"Razorpay reported: reason={signal.error_reason} source={signal.error_source} "
            f"step={signal.error_step} method={signal.method}"
        )
    diagnosis = store.latest_diagnosis(case.id)
    if diagnosis:
        parts.append(
            f"The agent diagnosed {diagnosis.klass.value} at confidence "
            f"{diagnosis.confidence}: {diagnosis.reasoning}"
        )
    for record in store.timeline(case.id):
        if record.decision_type == "reply":
            parts.append(f'The customer replied: "{record.inputs.get("reply", "")}"')
        if record.decision_type == "stop":
            parts.append(f"It stopped because: {record.action} ({', '.join(record.rules_fired)})")
    return "\n".join(parts)


def record_decision(
    store: Store, case: Case, action: str, note: str, *, new_state: CaseState | None = None,
    new_class: DiagnosisClass | None = None,
) -> None:
    now = windows.as_ist(datetime.now(tz=windows.IST))
    if new_state is not None:
        store.set_state(case.id, new_state)
    store.record(
        AuditRecord(
            case_id=case.id,
            at=now,
            decision_type="human-review",
            action=action,
            inputs={"note": note, "reclassified_to": new_class.value if new_class else None},
            rules_fired=["HUMAN_GATE"],
            human_actor=reviewer(),
            outcome=f"decided by {reviewer()}",
        )
    )
    print(f"  {DIM}recorded against {case.id}, attributed to {reviewer()}{RESET}")


def queue_of(store: Store) -> list[Case]:
    return [c for c in store.all_cases() if c.state is CaseState.HELD_FOR_HUMAN]


def draw_queue(store: Store, held: list[Case], *, arrived: list[Case]) -> None:
    now = windows.as_ist(datetime.now(tz=windows.IST))
    print(tui.clear(), end="")
    print(tui.banner(
        "REVIEWER — the human queue",
        f"reviewer: {reviewer()} · {now:%d %b %H:%M IST} · nothing here expires into a yes",
    ))

    if arrived:
        print()
        for case in arrived:
            print(f"  {tui.chip('NEW', tui.RED)} {case.id}   "
                  f"Rs {case.amount_rupees:,.0f}   {tui.paint(case.customer_ref, tui.DIM)}")

    print()
    if not held:
        print(tui.kv("waiting", "nothing is on a person's desk", tint=tui.GREEN))
        print()
        print(tui.paint("  The agent is handling everything itself. This stays open.", tui.DIM))
    else:
        rows = []
        for n, case in enumerate(held, 1):
            why = next(
                (r for r in reversed(store.timeline(case.id)) if r.decision_type == "stop"),
                None,
            )
            reason = ", ".join(why.rules_fired) if why else "unknown"
            rows.append((
                f"{n}.",
                case.id,
                f"Rs {case.amount_rupees:>7,.0f}",
                tui.paint(reason, tui.RED),
                tui.paint(tui.relative(case.opened_at, now), tui.DIM),
            ))
        print(tui.table(rows, headers=("", "case", "amount", "why it stopped", "opened")))

    print()
    print(tui.rule())
    print(tui.paint(
        f"  case number or id to open · q to close the session · refreshing every "
        f"{POLL_SECONDS:.0f}s",
        tui.DIM,
    ))


def wait_for_input(seconds: float) -> str | None:
    """A line if one is typed within `seconds`, otherwise None.

    The reviewer has to be able to sit and watch *and* to act, so the read
    cannot simply block. `select` gives both without a thread, and a thread here
    would need to share the store across two of them for no gain.
    """
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line.strip() if line else "q"
    ready, _, _ = select.select([sys.stdin], [], [], seconds)
    if not ready:
        return None
    line = sys.stdin.readline()
    return line.strip() if line else "q"


def decision_prompt(*, offer_insight: bool) -> None:
    print()
    print(tui.rule("your decision"))
    insight = f"  {tui.paint('i', tui.BOLD)}nsight (asks the model)   " if offer_insight else ""
    print(f"  {tui.paint('a', tui.BOLD)}pprove   {tui.paint('r', tui.BOLD)}eject   "
          f"re{tui.paint('c', tui.BOLD)}lassify   {insight}"
          f"{tui.paint('b', tui.BOLD)}ack")


def handle(store: Store, held: list[Case], choice: str) -> None:
    case = pick(held, choice)
    if case is None:
        print(tui.paint(f"  no case matching {choice!r}", tui.RED))
        wait_for_input(1.5)
        return

    print(tui.clear(), end="")
    print(tui.banner(f"REVIEWING {case.id}", "the raw material — a decision needs none of what follows"))
    show_case(store, case)

    # The prompt appears the moment the raw material does. It never waits on
    # the model, because the material above is already enough to decide from —
    # the same split the rest of this project makes between the shell and the
    # core, applied to what a reviewer is kept waiting on.
    offered_insight = False
    while True:
        decision_prompt(offer_insight=not offered_insight)
        action = (input("  action: ").strip().lower() or "b")

        if action.startswith("i") and not offered_insight:
            offered_insight = True
            show_briefing(store, case)
            continue

        if action.startswith("a"):
            note = input("  note: ").strip()
            record_decision(store, case, "approved", note, new_state=CaseState.OPEN)
        elif action.startswith("r") and not action.startswith(("rec", "recl")):
            note = input("  note: ").strip()
            record_decision(store, case, "rejected", note, new_state=CaseState.UNRECOVERED)
        elif action.startswith(("c", "rec")):
            print("  classes: " + ", ".join(c.value for c in DiagnosisClass))
            raw = input("  reclassify to: ").strip()
            try:
                klass = DiagnosisClass(raw)
            except ValueError:
                print(tui.paint("  not a class", tui.RED))
                wait_for_input(1.5)
                return
            note = input("  note: ").strip()
            record_decision(
                store, case, f"reclassified to {klass.value}", note,
                new_state=CaseState.OPEN, new_class=klass,
            )
            print(tui.paint(
                "  the override is labelled data: an auditable record of where the "
                "taxonomy was wrong", tui.DIM,
            ))
        # anything else, including 'b', leaves the case as it was
        break
    wait_for_input(2.0)


def main() -> int:
    if not SESSION_DB.exists():
        print(f"no session database at {SESSION_DB.name}.")
        print("Run `uv run python scripts/session.py` first, or")
        print("`uv run unhalted run-due` to create it.")
        return 1

    store = Store(str(SESSION_DB))
    seen: set[str] = {c.id for c in queue_of(store)}
    arrived: list[Case] = []

    try:
        while True:
            held = queue_of(store)
            ids = {c.id for c in held}
            fresh = [c for c in held if c.id not in seen]
            if fresh:
                arrived = fresh
            seen = ids

            draw_queue(store, held, arrived=arrived)
            choice = wait_for_input(POLL_SECONDS)

            if choice is None:
                arrived = []          # shown once, then it stops being news
                continue
            if choice.lower() in ("q", "quit"):
                print(tui.paint("\n  session closed by the reviewer.\n", tui.DIM))
                return 0
            if not choice:
                continue
            handle(store, held, choice)
    except (EOFError, KeyboardInterrupt):
        print(tui.paint("\n  session closed.\n", tui.DIM))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
