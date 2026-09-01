"""Capture real failed payments from Razorpay test mode as test fixtures.

A fixture written by the same person who wrote the parser proves only that the
two agree with each other. These come off Razorpay's infrastructure: real
payments, created through the hosted checkout with their published
error-scenario cards, fetched back through the API.

    uv run python scripts/capture_fixtures.py            # capture what exists
    uv run python scripts/capture_fixtures.py --list      # show without writing

Each fixture records where it came from — the payment id, when it was captured,
and the error fields Razorpay returned — so anyone can check it was not invented.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import UTC, datetime

import razorpay

ROOT = pathlib.Path(__file__).parent.parent
OUT = ROOT / "tests" / "fixtures" / "razorpay" / "captured"

#: The fields the pipeline reads. A capture missing any of them is recorded but
#: flagged, because a fixture that silently lacks what we diagnose on is worse
#: than no fixture.
REQUIRED = ("id", "status", "amount", "method", "error_reason", "error_source", "error_step")


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def client() -> razorpay.Client:
    load_env()
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key_id or not secret:
        sys.exit("no Razorpay credentials in .env")
    if not key_id.startswith("rzp_test_"):
        sys.exit(f"refusing to run against a non-test key ({key_id[:9]}...)")
    return razorpay.Client(auth=(key_id, secret))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="show what would be captured")
    args = ap.parse_args()

    c = client()
    payments = c.payment.all({"count": 100})
    failed = [p for p in payments.get("items", []) if p.get("status") == "failed"]

    if not failed:
        print("No failed payments on this account.")
        print("Create some by paying a link with Razorpay's error-scenario test cards,")
        print("then run this again. See docs/capturing-fixtures.md")
        return 1

    print(f"{len(failed)} failed payment(s) on the account\n")
    captured, incomplete = 0, []

    for p in failed:
        reason = p.get("error_reason") or "no_reason"
        method = p.get("method") or "unknown"
        missing = [f for f in REQUIRED if p.get(f) in (None, "")]
        marker = "  (missing: " + ", ".join(missing) + ")" if missing else ""
        print(f"  {p['id']}  {method:<12} {reason:<28} src={p.get('error_source')}{marker}")
        if missing:
            incomplete.append(p["id"])
        if args.list:
            continue

        OUT.mkdir(parents=True, exist_ok=True)
        record = {
            "captured_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "captured_from": "razorpay test mode, hosted checkout with an error-scenario card",
            "payment": p,
        }
        name = f"{method}_{reason}_{p['id']}.json"
        (OUT / name).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        captured += 1

    if args.list:
        return 0

    print(f"\nwrote {captured} fixture(s) to {OUT.relative_to(ROOT)}")
    if incomplete:
        print(f"note: {len(incomplete)} lack fields the pipeline reads: {', '.join(incomplete)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
