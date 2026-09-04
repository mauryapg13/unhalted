"""Run the batch and write the measurement report.

    uv run python scripts/run_batch.py            # 300 cases
    uv run python scripts/run_batch.py --count 500

No model calls: diagnosis on this batch resolves from the rules table, which is
the point being demonstrated rather than a shortcut.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from datetime import UTC, datetime

from unhalted.measure.generate import generate
from unhalted.measure.report import render, render_terminal, run_batch
from unhalted.store import Store

ROOT = pathlib.Path(__file__).parent.parent
REPORT = ROOT / "docs" / "batch-measurement.md"
#: The numbers behind the doc, so `unhalted report` can render a clean terminal
#: summary without re-running 300 cases just to print five lines. The doc
#: stays the source a reader opens for the argument behind each number.
SUMMARY = ROOT / "docs" / "batch-measurement.json"

_SCALAR_FIELDS = (
    "cases", "attempts", "attempts_in_restricted_window", "futile_attempts",
    "messages", "intervention_paise", "held_for_human", "closed_uneconomic",
)
_COUNTER_FIELDS = ("by_class", "by_rung", "by_confidence_band", "by_source")


def _plain(totals) -> dict:
    """`Totals` as plain JSON, without `dataclasses.asdict`.

    `asdict` rebuilds a dict field as `type(obj)(pairs)`, and `Counter`'s
    constructor treats an iterable of pairs as elements to *count* rather than
    as `(key, value)` pairs — so every Counter field would silently come back
    counting its own tuples instead of holding the original counts.
    """
    out = {name: getattr(totals, name) for name in _SCALAR_FIELDS}
    out.update({name: dict(getattr(totals, name)) for name in _COUNTER_FIELDS})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--holdout", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20260902)
    args = ap.parse_args()

    cases = generate(args.count, holdout_pct=args.holdout, seed=args.seed)
    print(f"running {len(cases)} cases through both policies...")

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(str(pathlib.Path(tmp) / "batch.db"))
        try:
            agent, base = run_batch(cases, store)
        finally:
            store.close()

    holdout = sum(1 for c in cases if c.holdout)
    total_paise = sum(c.signal.amount_paise for c in cases)
    generated_at = f"{datetime.now(tz=UTC):%Y-%m-%d %H:%M UTC}"

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(render(cases, agent, base))
    SUMMARY.write_text(json.dumps({
        "generated_at": generated_at,
        "cases_count": len(cases),
        "holdout": holdout,
        "total_paise": total_paise,
        "agent": _plain(agent),
        "base": _plain(base),
    }, indent=2))

    print()
    print(render_terminal(
        agent, base, cases_count=len(cases), holdout=holdout,
        total_paise=total_paise, generated_at=generated_at,
    ))
    print(f"\n  ({REPORT.relative_to(ROOT)} and {SUMMARY.relative_to(ROOT)} written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
