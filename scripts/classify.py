"""What the taxonomy says for five real, documented Razorpay failure reasons.

This is not a payment. It never touches a case, a store, or a webhook — it
calls `diagnose()` directly with the same `(method, error_reason, error_source)`
combinations `docs/capturing-fixtures.md` lists as Razorpay's own published
error-scenario test cards. That file's own three captured fixtures all carry
the same reason (`payment_failed`), which is why every rehearsal through
`scripts/session.py` has landed on the same class so far — this shows the
breadth the taxonomy actually covers, using the same rule table judges can
check against Razorpay's documentation, without needing four more real
webhooks captured first.

    uv run python scripts/classify.py
"""

from __future__ import annotations

from datetime import UTC, datetime

from unhalted import tui
from unhalted.core.diagnose import diagnose
from unhalted.core.scenarios import ERROR_SOURCE, METHOD, SCENARIOS
from unhalted.models import FailureSignal


def main() -> int:
    print(tui.banner(
        "TAXONOMY — five documented reasons, one rule table",
        "diagnose() called directly; nothing here is a payment or a case",
    ))
    print()

    diagnosed = []
    for reason, gloss in SCENARIOS:
        signal = FailureSignal(
            payment_id=f"pay_EXPLORE_{reason}", customer_ref="cust_explore",
            amount_paise=49900, occurred_at=datetime.now(tz=UTC),
            source="explore", method=METHOD, error_reason=reason,
            error_source=ERROR_SOURCE[reason],
        )
        diagnosed.append((reason, gloss, diagnose(signal)))

    rows = [
        (reason, d.klass.value, f"{d.confidence:.2f}", d.authority)
        for reason, _, d in diagnosed
    ]
    print(tui.table(rows, headers=("error_reason", "class", "confidence", "authority")))
    print()
    print(tui.paint(
        "  Compare against docs/capturing-fixtures.md's card table — capturing any one of these\n"
        "  for real needs a Razorpay test-mode payment link paid with the card it names, then\n"
        "  `uv run python scripts/capture_fixtures.py`. See that file for the full procedure.",
        tui.DIM,
    ))
    print()
    for reason, _, d in diagnosed:
        print(f"  {tui.paint(reason, tui.BOLD)}")
        print(f"    {tui.paint(d.reasoning, tui.DIM)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
