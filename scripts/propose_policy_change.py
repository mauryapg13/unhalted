"""Read a circular. Get a proposed diff against config/policy.yaml. Apply
nothing.

NPCI and RBI requirements change, and this system's enforcement lives in one
file for exactly that reason (see config/policy.yaml). This reads free text
describing a change — paste a circular, a notice, an internal memo — and
prints what the model thinks changed, checked against the words it was
actually given. It never writes to config/policy.yaml. A person reads the
proposal, and a person makes the edit, the same distance between
recommending and acting every other model call in this project keeps.

    uv run python scripts/propose_policy_change.py --file circular.txt
    pbpaste | uv run python scripts/propose_policy_change.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from unhalted import tui
from unhalted.core.policy_change import Proposal, propose


def render(result: Proposal) -> str:
    lines = [
        tui.banner(
            "PROPOSED — NOT APPLIED",
            "read the quote for each change against the source text yourself before editing "
            "config/policy.yaml; nothing here has written anything",
        ),
        "",
    ]

    if result.failed:
        lines.append(tui.paint(f"  could not read this text: {result.failure_reason}", tui.RED))
        return "\n".join(lines)

    if not result.changes:
        lines.append(tui.paint("  no supported field had a stated change in this text.", tui.DIM))
    for c in result.changes:
        lines += [
            f"  {tui.paint(c.field, tui.BOLD)}",
            f"    current:   {c.current_value!r}",
            f"    proposed:  {tui.paint(repr(c.proposed_value), tui.GREEN)}",
            f"    quote:     \"{c.quote}\"",
            f"    reasoning: {tui.paint(c.reasoning, tui.DIM)}",
            "",
        ]

    if result.dropped:
        lines.append(tui.paint("  refused before being shown above:", tui.RED))
        for d in result.dropped:
            lines.append(f"    - {d}")
        lines.append("")

    if result.unclear:
        lines.append(tui.paint("  mentioned, but no usable number given:", tui.AMBER))
        for u in result.unclear:
            lines.append(f"    - {u}")
        lines.append("")

    lines.append(tui.paint(f"  model cost: ${result.cost_usd:.4f} (OpenRouter usage.cost)", tui.DIM))
    lines.append(tui.paint(
        "  nothing has been written. Edit config/policy.yaml yourself for any change you "
        "accept — this script has no path to a filesystem write at all.", tui.DIM,
    ))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="read the circular from this file instead of stdin")
    args = ap.parse_args()

    text = pathlib.Path(args.file).read_text() if args.file else sys.stdin.read()

    if not text.strip():
        print("no text given — pipe one in, or pass --file")
        return 1

    print(render(propose(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
