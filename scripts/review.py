"""The human review queue.

Cases reach here when the agent declines to act alone: a diagnosis it could not
make confidently, a reply it could not read, a dispute it cannot settle, a
cancellation it must not execute on the customer's behalf, or hardship that
should never be handled by automation at all.

    uv run python scripts/review.py

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
import sys
from datetime import datetime

from unhalted.models import AuditRecord, Case, CaseState, DiagnosisClass
from unhalted.shell import windows
from unhalted.store import Store

ROOT = pathlib.Path(__file__).parent.parent
SESSION_DB = ROOT / "session.db"

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


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


def main() -> int:
    if not SESSION_DB.exists():
        print(f"no session database at {SESSION_DB.name}.")
        print("Run `uv run python scripts/session.py` first.")
        return 1

    store = Store(str(SESSION_DB))
    try:
        while True:
            held = [c for c in store.all_cases() if c.state is CaseState.HELD_FOR_HUMAN]
            print(f"\n{BOLD}Review queue{RESET}  {DIM}reviewer: {reviewer()}{RESET}")
            if not held:
                print(f"  {DIM}empty — nothing is waiting on a person{RESET}")
                return 0
            for n, c in enumerate(held, 1):
                print(f"  {n}. {c.id}   Rs {c.amount_rupees:.0f}   {c.customer_ref}")

            choice = input("\n  case (number or id, q to quit): ").strip()
            if choice.lower() in ("q", "quit", ""):
                return 0

            case = pick(held, choice)
            if case is None:
                print(f"  no case matching {choice!r}")
                continue

            show_case(store, case)
            print(f"\n  {BOLD}a{RESET}pprove   {BOLD}r{RESET}eject   "
                  f"re{BOLD}c{RESET}lassify   {BOLD}b{RESET}ack")
            action = input("  action: ").strip().lower()

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
                    print("  not a class")
                    continue
                note = input("  note: ").strip()
                record_decision(
                    store, case, f"reclassified to {klass.value}", note,
                    new_state=CaseState.OPEN, new_class=klass,
                )
                print(f"  {DIM}the override is labelled data: an auditable record of where the"
                      f" taxonomy was wrong{RESET}")
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
