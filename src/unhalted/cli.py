"""The command line. How a person reads what the agent did.

    unhalted case CASE-1AD69F26     one case, end to end
    unhalted cases                  what is open, held, closed
    unhalted queue                  what is waiting on a person
    unhalted report                 the batch numbers
    unhalted capabilities           what this account can actually do

The audit trail is the only account of what happened that anyone should trust,
and until now reading it meant writing a query. That is the gap this closes:
an auditor, a reviewer and whoever is debugging at two in the morning all need
the same thing, and none of them should need the schema.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from unhalted import config
from unhalted.models import AuditRecord, CaseState
from unhalted.store import Store

ROOT = pathlib.Path(__file__).parent.parent.parent

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def _plain() -> bool:
    return not sys.stdout.isatty()


def b(text: str) -> str:
    return text if _plain() else f"{BOLD}{text}{RESET}"


def d(text: str) -> str:
    return text if _plain() else f"{DIM}{text}{RESET}"


def open_store(path: str | None) -> Store:
    return Store(path or config.database_path())


# -- unhalted case ------------------------------------------------------------


def render_record(record: AuditRecord, *, verbose: bool) -> list[str]:
    who = ""
    if record.human_actor:
        who = f"  by {record.human_actor}"
    elif record.model_name:
        who = f"  model {record.model_name}"

    header = (
        f"  {record.at:%Y-%m-%d %H:%M}  {b(record.decision_type.upper()):<22} "
        f"{record.action}{d(who)}"
    )
    lines = [header]

    for rule in record.rules_fired:
        lines.append(d(f"      rule     {rule}"))
    if record.rule_version:
        lines.append(d(f"      version  {record.rule_version}"))
    if record.confidence is not None:
        lines.append(d(f"      conf     {record.confidence}"))

    if verbose:
        for key, value in (record.inputs or {}).items():
            if value not in (None, "", [], {}):
                lines.append(d(f"      {key:<9}{str(value)[:96]}"))
    elif record.inputs.get("reply"):
        lines.append(d(f'      said     "{record.inputs["reply"][:80]}"'))

    if record.outcome:
        lines.append(d(f"      ->       {record.outcome[:96]}"))
    return lines


def show_case(store: Store, case_id: str, *, verbose: bool) -> int:
    case = store.get_case(case_id)
    if case is None:
        matches = [c for c in store.all_cases()
                   if c.id.upper().removeprefix("CASE-").startswith(
                       case_id.upper().removeprefix("CASE-"))]
        if len(matches) != 1:
            print(f"no case matching {case_id!r}"
                  + (f" ({len(matches)} partial matches)" if matches else ""))
            return 1
        case = matches[0]

    print(f"\n{b(case.id)}   Rs {case.amount_rupees:,.0f}   {case.customer_ref}")
    print(d(f"  state {case.state.value}   opened {case.opened_at:%Y-%m-%d %H:%M %Z}   "
            f"retries {case.retry_count}"))

    for signal in store.signals(case.id):
        print(d(f"\n  from {signal.source}"))
        print(d(f"    {signal.payment_id}  {signal.method}  "
                f"reason={signal.error_reason}  source={signal.error_source}  "
                f"step={signal.error_step}"))

    diagnosis = store.latest_diagnosis(case.id)
    if diagnosis:
        print(f"\n  {b('diagnosis')}  {diagnosis.klass.value}   "
              f"confidence {diagnosis.confidence}   via {diagnosis.source.value}")
        print(d(f"    authority {diagnosis.authority}   taxonomy {diagnosis.taxonomy_version}"))
        print(d(f"    {diagnosis.reasoning}"))

    timeline = store.timeline(case.id)
    print(f"\n  {b('timeline')}  {len(timeline)} decisions")
    for record in timeline:
        for line in render_record(record, verbose=verbose):
            print(line)

    pending = store.pending_actions(case_id=case.id)
    print(f"\n  {b('pending')}  {len(pending)} automated action(s)")
    for action in pending:
        when = action["scheduled_for"] or "unscheduled"
        print(d(f"    {action['kind']:<24} {when}"))
    print()
    return 0


# -- unhalted cases / queue ---------------------------------------------------


def list_cases(store: Store, *, state: str | None) -> int:
    cases = store.all_cases()
    if state:
        cases = [c for c in cases if c.state.value == state]
    if not cases:
        print("no cases" + (f" in state {state!r}" if state else ""))
        return 0

    print(f"\n{b('cases')}  {len(cases)}")
    for case in cases:
        diagnosis = store.latest_diagnosis(case.id)
        klass = diagnosis.klass.value if diagnosis else "-"
        print(f"  {case.id}  Rs {case.amount_rupees:>7,.0f}  "
              f"{case.state.value:<18} {klass:<24} {d(case.customer_ref)}")
    print()
    return 0


def show_queue(store: Store) -> int:
    held = [c for c in store.all_cases() if c.state is CaseState.HELD_FOR_HUMAN]
    if not held:
        print("nothing is waiting on a person")
        return 0

    print(f"\n{b('waiting on a person')}  {len(held)}")
    for case in held:
        why = next(
            (r for r in reversed(store.timeline(case.id)) if r.decision_type == "stop"), None
        )
        reason = ", ".join(why.rules_fired) if why else "unknown"
        print(f"  {case.id}  Rs {case.amount_rupees:>7,.0f}  {d(reason)}")
    print(d("\n  review them with: uv run python scripts/review.py\n"))
    return 0


# -- unhalted report ----------------------------------------------------------


def show_report() -> int:
    report = ROOT / "docs" / "batch-measurement.md"
    if not report.exists():
        print("no batch measurement yet. Run: uv run python scripts/run_batch.py")
        return 1
    print(report.read_text())
    return 0


# -- unhalted capabilities ----------------------------------------------------


def show_capabilities() -> int:
    """What this deployment can actually do, and what it cannot.

    An absence should be inspectable rather than a silent hole — a reader of the
    README should be able to check the claims against the running system.
    """
    import os

    from unhalted.core.taxonomy import taxonomy_version

    rows = [
        ("Razorpay credentials",
         os.environ.get("RAZORPAY_KEY_ID", "").startswith("rzp_test_"), "test mode only"),
        ("Webhook secret", bool(config.webhook_secret()), "required to accept webhooks"),
        ("Model endpoint", bool(config.model_api_key()),
         f"{config.model_name() or 'unset'} — reply parsing, drafting, briefing"),
        ("Diagnosis taxonomy", True, taxonomy_version()),
    ]
    print(f"\n{b('capabilities')}")
    for name, ok, note in rows:
        mark = "yes" if ok else "no "
        print(f"  {mark}  {name:<24} {d(note)}")
    print(d("\n  UPI Autopay transport: absent — the rules are implemented and tested,"))
    print(d("  the account cannot be provisioned for it. See CHECKPOINTS.md.\n"))
    return 0


# -- entry point --------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="unhalted",
        description="Read what the mandate recovery agent did.",
        epilog=(
            "The audit trail is the only account of what happened that anyone should "
            "trust. Reading it should not require the schema."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", help="case database (default: $UNHALTED_DB)")
    sub = parser.add_subparsers(dest="command")

    case_cmd = sub.add_parser("case", help="print one case end to end")
    case_cmd.add_argument("case_id")
    case_cmd.add_argument("-v", "--verbose", action="store_true",
                          help="show every recorded input, not just the notable ones")

    cases_cmd = sub.add_parser("cases", help="list cases")
    cases_cmd.add_argument("--state", help="filter by state")

    sub.add_parser("queue", help="what is waiting on a person")
    sub.add_parser("report", help="the batch measurement")
    sub.add_parser("capabilities", help="what this deployment can do")

    args = parser.parse_args(argv)

    if args.command in (None,):
        parser.print_help()
        return 0
    if args.command == "report":
        return show_report()
    if args.command == "capabilities":
        return show_capabilities()

    store = open_store(args.db)
    try:
        if args.command == "case":
            return show_case(store, args.case_id, verbose=args.verbose)
        if args.command == "cases":
            return list_cases(store, state=args.state)
        if args.command == "queue":
            return show_queue(store)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
