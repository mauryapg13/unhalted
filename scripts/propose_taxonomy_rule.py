"""What's held for a human with no rule to explain why, clustered — and, for
one chosen cluster, a proposed rule grounded in Razorpay's own documentation.

Every case `diagnose()` cannot match sits `UNKNOWN`, held for a person, and
until now that was the end of it: nothing fed the gap back toward closing
it. This is two views, not one command doing both, because clustering is
plain grouping — no model needed — and only the proposal step touches one:

    uv run python scripts/propose_taxonomy_rule.py
        # what's unclassified right now, grouped and counted

    uv run python scripts/propose_taxonomy_rule.py \\
        --method card --reason risk_check_failed --file razorpay-doc-excerpt.txt
        # a proposed rule for one of those, grounded in the text you supply

Never writes to core/taxonomy.py. A proposal is a diff to consider adding by
hand, the same distance between recommending and acting every model call in
this project keeps.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections import Counter

from unhalted import config, tui
from unhalted.core.taxonomy_proposal import RuleProposal, propose
from unhalted.models import DiagnosisClass
from unhalted.store import Store


def unclassified_clusters(store: Store) -> list[tuple[tuple[str, str, str, str], int]]:
    """(method, reason, source, step) -> how many held cases share it, most
    common first. Only cases diagnose() genuinely could not match — held on
    low confidence for a reason the taxonomy does resolve is a different
    thing and not this script's concern."""
    counter: Counter[tuple[str, str, str, str]] = Counter()
    for case in store.all_cases():
        diagnosis = store.latest_diagnosis(case.id)
        if diagnosis is None or diagnosis.klass is not DiagnosisClass.UNKNOWN:
            continue
        if "no taxonomy entry" not in diagnosis.reasoning:
            continue
        signals = store.signals(case.id)
        if not signals:
            continue
        s = signals[0]
        key = (s.method or "", s.error_reason or "", s.error_source or "", s.error_step or "")
        counter[key] += 1
    return counter.most_common()


def render_clusters(clusters: list[tuple[tuple[str, str, str, str], int]]) -> str:
    if not clusters:
        return tui.paint("  nothing unclassified right now.", tui.DIM)
    rows = [
        (str(n), method, reason, source or "-", step or "-")
        for (method, reason, source, step), n in clusters
    ]
    return tui.table(rows, headers=("cases", "method", "error_reason", "source", "step"))


def render_proposal(p: RuleProposal) -> str:
    lines = [
        tui.banner(
            "PROPOSED RULE — NOT APPLIED",
            "check the quote against the text yourself before editing core/taxonomy.py",
        ),
        "",
    ]
    if p.failed:
        lines.append(tui.paint(f"  could not propose one: {p.failure_reason}", tui.RED))
        return "\n".join(lines)

    if not p.proposable:
        lines.append(tui.paint(f"  no rule proposed: {p.rationale}", tui.AMBER))
        lines.append(tui.paint(
            "  held for a human stands, honestly — the text given does not settle this.", tui.DIM,
        ))
        return "\n".join(lines)

    key = f"{p.method} | {p.error_reason} | {p.error_source or '*'} | {p.error_step or '*'}"
    lines += [
        f"  key:        {key}",
        f"  class:      {tui.paint(p.klass.value, tui.GREEN)}",
        f"  directness: {'DIRECT' if p.directness == 1.0 else 'INFERRED'}",
        f'  quote:      "{p.quote}"',
        f"  rationale:  {tui.paint(p.rationale, tui.DIM)}",
        "",
        tui.paint(
            "  nothing written. Add this to core/taxonomy.py's TABLE yourself if you agree.",
            tui.DIM,
        ),
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--method")
    ap.add_argument("--reason")
    ap.add_argument("--source")
    ap.add_argument("--step")
    ap.add_argument("--file", help="Razorpay documentation excerpt to ground the proposal in")
    args = ap.parse_args()

    store = Store(config.database_path())
    try:
        if not args.method or not args.reason:
            print(tui.banner(
                "UNCLASSIFIED — clustered",
                f"{config.database_path()} · held cases with no matching taxonomy rule",
            ))
            print()
            print(render_clusters(unclassified_clusters(store)))
            print()
            print(tui.paint(
                "  --method and --reason (plus Razorpay's own documentation via --file) "
                "propose a rule for one of these.", tui.DIM,
            ))
            return 0

        if args.file:
            doc_text = pathlib.Path(args.file).read_text()
        else:
            if sys.stdin.isatty():
                print("Paste Razorpay's documentation for this reason, then press Ctrl-D "
                      "on its own line when done (Ctrl-C to cancel):", file=sys.stderr)
            doc_text = sys.stdin.read()

        if not doc_text.strip():
            print("no documentation text given — pipe one in, or pass --file")
            return 1

        result = tui.spin(
            f"checking Razorpay's documentation for {args.reason!r}",
            lambda: propose(
                method=args.method, error_reason=args.reason,
                error_source=args.source, error_step=args.step, doc_text=doc_text,
            ),
        )
        print(render_proposal(result))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
