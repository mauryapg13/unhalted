"""The command line. How a person reads what the agent did.

    unhalted case CASE-1AD69F26     one case, end to end
    unhalted compare CASE-1AD69F26  the same case under Razorpay's retry policy
    unhalted cases                  what is open, held, closed
    unhalted queue                  what is waiting on a person
    unhalted report                 the batch numbers
    unhalted breakeven              what the money argument rests on
    unhalted run-due                execute the actions that have come due
    unhalted capabilities           what this account can actually do
    unhalted policy                 the currently loaded policy — every threshold enforced

The audit trail is the only account of what happened that anyone should trust,
and until now reading it meant writing a query. That is the gap this closes:
an auditor, a reviewer and whoever is debugging at two in the morning all need
the same thing, and none of them should need the schema.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import textwrap

from unhalted import clock, config
from unhalted.measure.compare import LEGEND, compare, differences
from unhalted.measure.outcomes import breakeven, classify, envelope, render_outcomes
from unhalted.models import AuditRecord, Case, CaseState
from unhalted.runner import run_due
from unhalted.store import OrphanedWriteAheadLog, Store

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


def find_case(store: Store, case_id: str) -> Case | None:
    """Whatever the person reasonably typed. A prefix is enough if it is unique."""
    case = store.get_case(case_id)
    if case is not None:
        return case

    wanted = case_id.upper().removeprefix("CASE-")
    matches = [
        c for c in store.all_cases()
        if c.id.upper().removeprefix("CASE-").startswith(wanted)
    ]
    if len(matches) != 1:
        print(f"no case matching {case_id!r}"
              + (f" ({len(matches)} partial matches)" if matches else ""))
        return None
    return matches[0]


def show_case(store: Store, case_id: str, *, verbose: bool) -> int:
    case = find_case(store, case_id)
    if case is None:
        return 1

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


# -- unhalted compare ---------------------------------------------------------

#: Two columns and a clock. Wide enough for a rule name, narrow enough for a
#: terminal nobody has resized.
_TIME_W, _COL_W = 17, 42


def _cell(text: str) -> list[str]:
    return textwrap.wrap(text, _COL_W) or [""]


def _column_line(when: str, left: str, right: str, *, mark: bool = False) -> str:
    gutter = "|" if mark else " "
    return f"  {when:<{_TIME_W}}{gutter} {left:<{_COL_W}}  {right}"


def show_comparison(store: Store, case_id: str) -> int:
    """The same failure under both policies, side by side.

    Nothing here is simulated for the agent's side: it is the audit trail. The
    baseline's side is Razorpay's documented behaviour replayed over the same
    signal. Neither column claims a recovery.
    """
    case = find_case(store, case_id)
    if case is None:
        return 1

    result = compare(
        case,
        store.signals(case.id),
        store.latest_diagnosis(case.id),
        store.timeline(case.id),
    )

    print(f"\n{b(result.case_id)}   Rs {result.amount_rupees:,.0f}   {result.customer_ref}")
    if result.signal is not None:
        print(d(f"  Razorpay said: reason={result.signal.error_reason}  "
                f"source={result.signal.error_source}  step={result.signal.error_step}"))
    if result.diagnosis is not None:
        model = result.diagnosis.model_name or "no model call"
        print(d(f"  agent diagnosed {result.diagnosis.klass.value} at "
                f"{result.diagnosis.confidence} via {result.diagnosis.source.value} ({model})"))

    print()
    print(_column_line("", b("Razorpay's retry policy"), b("unhalted")))
    print(d("  " + "-" * (_TIME_W + 2 * _COL_W + 4)))

    for event in result.events:
        when = f"{event.at:%d %b %H:%M}" if event.at else ""
        left, right = _cell(event.baseline), _cell(event.agent)
        for n in range(max(len(left), len(right))):
            print(_column_line(
                when if n == 0 else "",
                left[n] if n < len(left) else "",
                right[n] if n < len(right) else "",
                mark=event.marked and n == 0,
            ).rstrip())

    print(_column_line("", b(result.base.outcome), b(result.agent.outcome)).rstrip())

    print(f"\n  {b('counted')}   {d('what each policy did, with nothing assumed about outcomes')}")
    print(d(f"    {'':<30}{'agent':>8}{'baseline':>10}"))
    for label, agent_n, base_n, note in differences(result):
        print(f"    {label:<30}{agent_n:>8}{base_n:>10}   {d(note)}".rstrip())

    used = [m for m in LEGEND if any(m in e.baseline for e in result.events)]
    if used:
        print()
        for marker in used:
            print(d(f"    {marker:<14}{LEGEND[marker]}"))

    moved = result.agent.corrected_into_window
    if moved:
        print(d(f"\n  {moved} retry {'was' if moved == 1 else 'were'} moved out of a restricted "
                f"band rather than cancelled — the shell honours"))
        print(d("  the recommendation as closely as NPCI permits, and records that it had to."))

    print(d("\n  Neither column says what was recovered. That needs an outcome model,"))
    print(d("  and whoever writes one decides the comparison.\n"))
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


# -- unhalted run-due ---------------------------------------------------------


def run_due_actions(store: Store, at: str | None = None) -> int:
    """Execute whatever has come due, once.

    Safe to run at any moment, including twice in the same second: a pass that
    finds nothing due does nothing. That is what lets the same function sit
    behind a cron entry, an HTTP request, or a person typing this.
    """
    try:
        now, note = clock.resolve(at)
    except clock.BadTime as exc:
        print(exc)
        return 2
    if note:
        print(note)
    print(run_due(store, now=now).render())
    return 0


# -- unhalted breakeven -------------------------------------------------------


def show_breakeven(store: Store) -> int:
    """The money argument, computed from whatever cases are actually stored.

    Not a forecast. Every line is arithmetic on a measured intervention cost and
    a failure class Razorpay documents.
    """
    items = []
    for case in store.all_cases():
        diagnosis = store.latest_diagnosis(case.id)
        if diagnosis is None:
            continue
        items.append((case.amount_paise, diagnosis.klass))

    if not items:
        print("no diagnosed cases yet. Run the batch, or drive one through the pipeline.")
        return 1

    exposure = classify(items)
    print(render_outcomes(exposure, breakeven(exposure), envelope(exposure)))
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


def _fmt_td(td) -> str:
    seconds = int(td.total_seconds())
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def show_policy() -> int:
    """The policy actually in effect right now — not the file, the loaded,
    validated values every enforcement path reads. A reader who wants to
    verify a claim in the README, or check what a proposed change from
    `scripts/propose_policy_change.py` would be changing, should not have to
    open config/policy.yaml and parse it by eye.
    """
    from unhalted import policy as policy_mod

    p = policy_mod.POLICY
    print(f"\n{b('policy')}  {d(f'loaded from {config.policy_path()}')}")
    print(f"  {d('version')}  {p.version}")

    print(f"\n  {b('NPCI execution bands')}  {d(f'({p.npci_rule_version})')}")
    for start, end in p.npci_restricted_bands:
        print(f"    {start:%H:%M}-{end:%H:%M} IST  {d('restricted, UPI Autopay only')}")

    print(f"\n  {b('contact hours')}")
    print(f"    {p.contact_open:%H:%M}-{p.contact_close:%H:%M} IST")

    print(f"\n  {b('retries')}")
    print(f"    cap  {p.retry_cap} per billing cycle")
    for klass, tiers in p.backoff_raw.items():
        print(f"    {klass:<24} " + ", ".join(_fmt_td(t) for t in tiers))
    print(f"    {'(no schedule of its own)':<24} {_fmt_td(p.default_backoff)}")

    print(f"\n  {b('confidence thresholds')}")
    print(f"    auto-execute             >= {p.confidence_auto_execute:.2f}")
    print(f"    auto-execute-sampled-qa  >= {p.confidence_sampled_qa:.2f}")
    print("    hold-for-human           below that")

    print(f"\n  {b('reply policy')}  {d(f'({p.reply_rule_version})')}")
    print(f"    acts-on-money  >= {p.reply_acts_on_money:.2f}")
    print(f"    protective     >= {p.reply_protective:.2f}")
    print(f"    cancellation   >= {p.reply_cancellation:.2f}")

    print(f"\n  {b('ladder costs')}  {d(f'({p.ladder_rule_version})')}")
    for slug, paise in p.ladder_rung_costs_paise.items():
        print(f"    {slug:<24} Rs {paise / 100:.0f}")

    print(f"\n  {b('mandate limits')}  {d(f'({p.limit_rule_version})')}")
    print(f"    frictionless UPI            Rs {p.frictionless_upi_paise / 100:,.0f}")
    print(f"    frictionless UPI (BFSI)     Rs {p.frictionless_upi_bfsi_paise / 100:,.0f}")
    print(f"    UPI mandate max             Rs {p.upi_mandate_max_paise / 100:,.0f}")
    print(f"    card recurring max          Rs {p.card_recurring_max_paise / 100:,.0f}")
    print(f"    emandate max                Rs {p.emandate_max_paise / 100:,.0f}")
    print()
    return 0


# -- entry point --------------------------------------------------------------


#: Flags that mean the same thing wherever they appear.
#:
#: argparse puts an option on the parser it was declared against, so declaring
#: these once at the top makes `unhalted --at X run-due` correct and
#: `unhalted run-due --at X` an error. That is a distinction nobody should have
#: to learn, and I walked into it myself within a minute of adding the flag.
#:
#: Every subcommand gets them too, with `SUPPRESS` as the default so an absent
#: flag leaves the attribute unset rather than overwriting what the top-level
#: parser already read. A plain `default=None` on the subparser silently wins,
#: which is the argparse trap this exists to avoid.
def add_global_flags(
    parser: argparse.ArgumentParser, *, on_subcommand: bool = False
) -> None:
    default = argparse.SUPPRESS if on_subcommand else None
    parser.add_argument(
        "--db", default=default, help="case database (default: $UNHALTED_DB)"
    )
    parser.add_argument(
        "--at",
        default=default,
        metavar="'YYYY-MM-DD HH:MM'",
        help="evaluate the rules against this time (IST) instead of now. "
             "For rehearsal and testing; it announces itself loudly, and is not "
             "for recording.",
    )


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
    add_global_flags(parser)
    sub = parser.add_subparsers(dest="command")

    def command(name: str, **kwargs) -> argparse.ArgumentParser:
        """Add a subcommand that also accepts the global flags."""
        p = sub.add_parser(name, **kwargs)
        add_global_flags(p, on_subcommand=True)
        return p

    case_cmd = command("case", help="print one case end to end")
    case_cmd.add_argument("case_id")
    case_cmd.add_argument("-v", "--verbose", action="store_true",
                          help="show every recorded input, not just the notable ones")

    cases_cmd = command("cases", help="list cases")
    cases_cmd.add_argument("--state", help="filter by state")

    command("queue", help="what is waiting on a person")
    comparison = command(
        "compare", help="the same case under Razorpay's documented retry policy"
    )
    comparison.add_argument("case_id")

    command("run-due", help="execute the actions that have come due")
    command("breakeven", help="what the money argument rests on")
    command("report", help="the batch measurement")
    command("capabilities", help="what this deployment can do")
    command("policy", help="the currently loaded policy — every threshold this system enforces")

    args = parser.parse_args(argv)

    if args.command in (None,):
        parser.print_help()
        return 0

    try:
        return dispatch(args)
    except OrphanedWriteAheadLog as exc:
        # A message, not a traceback. The remedy is in the text and the reader
        # is somebody resetting a database, not somebody debugging this file.
        print(exc)
        return 2


def dispatch(args) -> int:
    if args.command == "compare":
        store = open_store(args.db)
        try:
            return show_comparison(store, args.case_id)
        finally:
            store.close()

    if args.command == "run-due":
        store = open_store(args.db)
        try:
            return run_due_actions(store, args.at)
        finally:
            store.close()

    if args.command == "breakeven":
        store = open_store(args.db)
        try:
            return show_breakeven(store)
        finally:
            store.close()

    if args.command == "report":
        return show_report()
    if args.command == "capabilities":
        return show_capabilities()
    if args.command == "policy":
        return show_policy()

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
