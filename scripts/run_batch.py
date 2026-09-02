"""Run the batch and write the measurement report.

    uv run python scripts/run_batch.py            # 300 cases
    uv run python scripts/run_batch.py --count 500

No model calls: diagnosis on this batch resolves from the rules table, which is
the point being demonstrated rather than a shortcut.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

from unhalted.measure.generate import generate
from unhalted.measure.report import render, run_batch
from unhalted.store import Store

ROOT = pathlib.Path(__file__).parent.parent
REPORT = ROOT / "docs" / "batch-measurement.md"


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

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(render(cases, agent, base))

    print(f"\n  {'':<38}{'agent':>8}{'baseline':>10}")
    for label, a, b in (
        ("debit attempts scheduled", agent.attempts, base.attempts),
        ("attempts a retry could not fix", agent.futile_attempts, base.futile_attempts),
        ("attempts in NPCI restricted bands", agent.attempts_in_restricted_window,
         base.attempts_in_restricted_window),
        ("cases held for a human", agent.held_for_human, 0),
        ("cases closed as uneconomic", agent.closed_uneconomic, 0),
    ):
        print(f"  {label:<38}{a:>8}{b:>10}")

    print(f"\n  confidence bands: {dict(agent.by_confidence_band)}")
    print(f"  report written to {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
