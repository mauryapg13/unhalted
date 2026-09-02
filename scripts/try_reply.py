"""Put a customer reply through the parser and the shell, and show both.

Held-out replies are the only test data that cannot have been tuned for. This
exists so someone who has not seen the prompt can throw sentences at it and
watch what the agent would actually do.

    uv run python scripts/try_reply.py "salary aane do, 2 tarikh ko"
    uv run python scripts/try_reply.py            # then type replies, one per line

It shows the whole chain, because the model's reading is only half the story —
the shell's thresholds and precedence decide what actually happens, and they
routinely refuse what the model proposed.
"""

from __future__ import annotations

import sys
from datetime import date

from unhalted.core.reply import parse
from unhalted.shell.replies import ACTS_ON_MONEY, CANCELLATION, PROTECTIVE, decide, validate_date

TODAY = date(2026, 9, 20)
CONTEXT = f"A subscription renewal of Rs 499 failed. Today is {TODAY}."

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def show(reply: str) -> None:
    print(f"\n{BOLD}{reply}{RESET}")
    parsed = parse(reply, context=CONTEXT)

    if parsed.failed:
        print(f"  {DIM}model{RESET}  parse failed after {parsed.attempts} attempts "
              f"— {parsed.failure_reason}")
    else:
        note = f"  ({parsed.attempts} attempts)" if parsed.attempts > 1 else ""
        print(f"  {DIM}model{RESET}  language={parsed.language}  "
              f"sentiment={parsed.sentiment.value}{note}")
        if not parsed.intents:
            print("         no intents read")
        for i in parsed.intents:
            gate = (
                "acts" if i.confidence >= ACTS_ON_MONEY else
                "protective only" if i.confidence >= PROTECTIVE else "below every threshold"
            )
            if i.type.value == "cancellation-request":
                gate = "acts" if i.confidence >= CANCELLATION else f"below the {CANCELLATION} bar"
            print(f"         {i.type.value:<22} {i.confidence:<5} [{gate}]")
            if i.evidence:
                print(f"           {DIM}evidence: \"{i.evidence}\"{RESET}")
        if parsed.payment_date_raw:
            check = validate_date(parsed.payment_date_raw, today=TODAY)
            verdict = "accepted" if check.accepted else f"REFUSED — {check.reason}"
            print(f"         date proposed {parsed.payment_date_raw}  [{verdict}]")

    out = decide(parsed, today=TODAY)
    print(f"  {DIM}shell{RESET}  stop={out.stop_code or '-'}  "
          f"realign={out.realign_to or '-'}  human={out.needs_human}")
    for rule in out.rules_fired:
        print(f"         {rule}")
    for reason in out.reasons:
        print(f"         {DIM}{reason}{RESET}")


def main() -> int:
    if len(sys.argv) > 1:
        for reply in sys.argv[1:]:
            show(reply)
        return 0

    print("Type a reply and press enter. Ctrl-D to finish.")
    for line in sys.stdin:
        line = line.strip()
        if line:
            show(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
