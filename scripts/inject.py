"""Run one of Razorpay's documented card scenarios through the real pipeline.

`scripts/classify.py` shows what the taxonomy says for these five reasons but
never touches a case — nothing there is auditable because nothing happened.
This is the other half: it calls the real `handle_failure()` against the real
store, so a real case opens, a real diagnosis is recorded, a real action gets
scheduled, and the scheduler and reviewer terminals see it exactly as they
would anything else.

The one thing this is not: a webhook. Nothing arrived from Razorpay — the
signal is built locally from the same `(method, error_reason, error_source)`
Razorpay's own test cards produce (see `docs/capturing-fixtures.md`), and the
banner says so every time, the same way a forced `--at` announces itself
rather than silently reaching a recording. A genuinely captured payment still
needs that file's real procedure; this exists for what that procedure cannot
give quickly — showing several different real, audited cases without waiting
on five separate captures.

    uv run python scripts/inject.py insufficient_fund
    uv run python scripts/inject.py --list        # numbered menu, choose interactively
"""

from __future__ import annotations

import argparse
import sys

from unhalted import clock, config, tui
from unhalted.agent import handle_failure
from unhalted.core.scenarios import ERROR_SOURCE, METHOD, SCENARIOS
from unhalted.models import FailureSignal
from unhalted.shell import windows
from unhalted.store import Store

AMOUNT_PAISE = 49900


def choose(known: dict[str, str]) -> str | None:
    """The numbered menu. Accepts a number or the reason itself; blank or EOF
    exits without picking anything, the same as Ctrl-D anywhere else here."""
    print("known scenarios (docs/capturing-fixtures.md's card table):")
    numbered = list(known.items())
    for i, (reason, gloss) in enumerate(numbered, start=1):
        print(f"  {i}. {reason:<24} {gloss}")
    print()
    try:
        raw = input("  choose a number, or type the reason: ").strip()
    except EOFError:
        return None
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(numbered):
        return numbered[int(raw) - 1][0]
    if raw in known:
        return raw
    print(f"  not a known scenario or number: {raw!r}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reason", nargs="?", help="one of the scenarios below; omit to choose")
    ap.add_argument("--list", action="store_true",
                    help="same as omitting the reason: show the menu and choose")
    ap.add_argument("--at", metavar="'YYYY-MM-DD HH:MM'",
                    help="rehearse against this time (IST). Announces itself.")
    args = ap.parse_args()

    known = dict(SCENARIOS)
    reason = args.reason
    if args.list or not reason:
        reason = choose(known)
        if reason is None:
            return 1

    if reason not in known:
        print(f"not a known scenario: {reason!r}. Run --list to see them.")
        return 1

    try:
        stated, note = clock.resolve(args.at)
    except clock.BadTime as exc:
        sys.exit(str(exc))
    now = windows.as_ist(stated)

    print(tui.banner(
        "INJECTED — a real case, not a real webhook",
        "built locally from a documented Razorpay test-card scenario; see the script's own "
        "docstring",
    ))
    if note:
        print(note)

    store = Store(config.database_path())
    signal = FailureSignal(
        payment_id=f"pay_INJECTED_{reason}",
        customer_ref=f"cust_injected_{reason}",
        amount_paise=AMOUNT_PAISE,
        occurred_at=now,
        source="inject",
        method=METHOD,
        error_reason=reason,
        error_source=ERROR_SOURCE,
    )
    existing = store.case_for_payment(signal.payment_id)
    case = handle_failure(store, signal, now=now)
    diagnosis = store.latest_diagnosis(case.id)

    print()
    print(f"  {tui.paint(reason, tui.BOLD)} — {known[reason]}")
    if existing:
        print(f"  {tui.paint('already existed; matched back to it, not duplicated', tui.DIM)}")
    print(f"  {case.id}   state={case.state.value}")
    print(f"  {diagnosis.klass.value}  confidence {diagnosis.confidence}  "
          f"authority {diagnosis.authority}")
    print(f"  {tui.paint(diagnosis.reasoning, tui.DIM)}")
    print()
    hint = (
        f"  uv run unhalted case {case.id}\n"
        f"  uv run python scripts/review.py     (if it needs a person)\n"
        f"  uv run python scripts/schedule.py   (to watch what got scheduled)"
    )
    print(f"  {tui.paint('this is a real, audited case:', tui.DIM)}")
    print(tui.paint(hint, tui.DIM))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
