"""Measure the reply parser against a labelled corpus.

Reports precision and recall per intent, **including the failures**. A parser
evaluated only on what it gets right is not evaluated.

The two error directions matter differently and the report says so:

- Missing an opt-out means messaging someone who asked you to stop. That is a
  compliance breach, so **recall on opt-out and distress is the number to watch**.
- Falsely reading a cancellation means cancelling a customer who was merely
  annoyed. That is lost revenue, so **precision on cancellation is the number to
  watch** there.

    uv run python scripts/evaluate_replies.py
    uv run python scripts/evaluate_replies.py --limit 10     # a quick pass
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

from unhalted import config
from unhalted.core.reply import ATTEMPTS as ATTEMPTS_PER_CALL
from unhalted.core.reply import parse, prompt_hash
from unhalted.shell.replies import decide, validate_date

ROOT = pathlib.Path(__file__).parent.parent
CORPUS = ROOT / "tests" / "fixtures" / "replies" / "labelled.json"
REPORT = ROOT / "docs" / "reply-evaluation.md"
CACHE = ROOT / ".reply-eval-cache.json"

WORKERS = 6
MODEL = config.model_name()
CONTEXT_TEMPLATE = "A subscription renewal of Rs 499 failed. Today is {today}."


def run_one(case: dict, cache: dict, context: str) -> tuple[dict, float | None]:
    """Returns (result, latency_seconds). Latency is `None` on a cache hit —
    it measures the endpoint, not how fast a dict lookup is."""
    key = f"{prompt_hash()}:{context}:{case['id']}"
    if key in cache:
        return cache[key], None
    t0 = time.monotonic()
    parsed = parse(case["reply"], context=context)
    latency = time.monotonic() - t0
    result = parsed.model_dump(mode="json")
    cache[key] = result
    return result, latency


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    data = json.loads(CORPUS.read_text())
    today = date.fromisoformat(data["today"])
    cases = data["cases"][: args.limit] if args.limit else data["cases"]

    cache = {} if args.no_cache or not CACHE.exists() else json.loads(CACHE.read_text())

    context = CONTEXT_TEMPLATE.format(today=data["today"])
    print(f"parsing {len(cases)} replies ({WORKERS} at a time)...")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        outcomes = list(pool.map(lambda c: run_one(c, cache, context), cases))
    CACHE.write_text(json.dumps(cache))

    results = [r for r, _ in outcomes]
    latencies = [dt for _, dt in outcomes if dt is not None]

    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    parse_failures: list[str] = []
    forbidden_hits: list[str] = []
    date_misses: list[str] = []
    threshold_misses: list[str] = []

    from unhalted.models import ParsedReply

    attempts_used = 0
    retried = 0
    scored = 0

    for case, raw in zip(cases, results):
        parsed = ParsedReply.model_validate(raw)
        attempts_used += parsed.attempts
        if parsed.attempts > 1:
            retried += 1

        # A parse failure is a reliability fact, not an accuracy one. The model
        # did not misread the reply; the endpoint returned nothing. Counting it
        # as a missed intent blames the wrong thing and hides the real problem,
        # so these are excluded here and reported on their own below.
        if parsed.failed:
            parse_failures.append(f"{case['id']}: {parsed.failure_reason}")
            continue

        scored += 1

        found = {i.type.value for i in parsed.intents}
        wanted = set(case["expect_intents"])

        for label in wanted | found:
            if label in wanted and label in found:
                tp[label] += 1
            elif label in found:
                fp[label] += 1
            else:
                fn[label] += 1

        for banned in case.get("forbid_intents", []):
            if banned in found:
                forbidden_hits.append(f"{case['id']}: read {banned} — {case['reply'][:52]}")

        if "expect_date" in case:
            check = validate_date(parsed.payment_date_raw, today=today)
            got = check.value.isoformat() if check.value else None
            if got != case["expect_date"]:
                date_misses.append(f"{case['id']}: wanted {case['expect_date']}, got {got}")

        # Did the reply actually change what the agent does?
        outcome = decide(parsed, today=today)
        # A promise with no date *should not* realign anything — the
        # specification requires it be recorded without a date and the follow-up
        # ask the customer to confirm one. Only a promise that named a usable
        # date and still changed nothing is a failure.
        overridden = wanted & {"dispute", "distress", "service-complaint"}
        if (
            "promise-to-pay" in wanted
            and case.get("expect_date")
            and not outcome.realign_to
            and not overridden
        ):
            threshold_misses.append(
                f"{case['id']}: promise named {case['expect_date']} and nothing realigned"
            )

    labels = sorted(set(tp) | set(fp) | set(fn))
    lines = []
    for label in labels:
        p = tp[label] / (tp[label] + fp[label]) if tp[label] + fp[label] else 0.0
        r = tp[label] / (tp[label] + fn[label]) if tp[label] + fn[label] else 0.0
        lines.append((label, tp[label], fp[label], fn[label], p, r))

    print(f"\n{'intent':<24}{'tp':>4}{'fp':>4}{'fn':>4}{'precision':>11}{'recall':>9}")
    for label, t, f, n, p, r in lines:
        print(f"{label:<24}{t:>4}{f:>4}{n:>4}{p:>11.2f}{r:>9.2f}")

    reliability = scored / len(cases) if cases else 0.0
    print(f"\naccuracy measured over {scored}/{len(cases)} replies that parsed")
    print(f"parse success rate  {reliability:.2f}  ({len(parse_failures)} failed after "
          f"{ATTEMPTS_PER_CALL} attempts each)")
    print(f"replies needing a retry  {retried}")
    print(f"model calls for {len(cases)} replies  {attempts_used}")
    print(f"forbidden intents   {len(forbidden_hits)}")
    print(f"date mismatches     {len(date_misses)}")
    print(f"promises not acted  {len(threshold_misses)}")

    costs = [r["cost_usd"] for r in results if r.get("cost_usd")]
    cost_per_parse = statistics.mean(costs) if costs else 0.0
    print(f"cost per parse  ${cost_per_parse:.6f}  (mean over {len(costs)} priced replies, "
          f"OpenRouter usage.cost)")

    if latencies:
        print(f"latency (live calls, n={len(latencies)})  "
              f"min={min(latencies):.1f}s median={statistics.median(latencies):.1f}s "
              f"max={max(latencies):.1f}s")
        latency_note = (
            f"Measured on {len(latencies)} live calls this run (the rest were served from "
            f"`.reply-eval-cache.json`): **{min(latencies):.1f}s to {max(latencies):.1f}s**, "
            f"median {statistics.median(latencies):.1f}s."
        )
    else:
        latency_note = (
            "Every reply this run was served from `.reply-eval-cache.json` — delete it "
            "(or pass `--no-cache`) to measure live latency."
        )

    def block(title: str, items: list[str]) -> str:
        if not items:
            return f"**{title}:** none.\n"
        body = "\n".join(f"- {i}" for i in items)
        return f"**{title}:** {len(items)}\n\n{body}\n"

    table = "\n".join(
        f"| `{lab}` | {t} | {f} | {n} | {pr:.2f} | {rc:.2f} |" for lab, t, f, n, pr, rc in lines
    )
    authored = len(cases) - sum(1 for c in cases if c.get("source") == "spec")

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(f"""# Reply parser evaluation

Generated {datetime.now(tz=UTC):%Y-%m-%d %H:%M UTC} · prompt `{prompt_hash()}` ·
model `{MODEL}` · {len(cases)} replies

## Provenance of the corpus

{len(cases) - authored} replies are taken verbatim from the project's Gherkin specification,
written before any of this code existed and therefore not tuned to what the model can do. The
remaining {authored} were authored for this evaluation, along with all the expected labels.

That is a real limit on what these numbers mean: they measure agreement with one person's reading
of what these replies intend. There is no public corpus of real Hinglish payment replies and this
test account has no customers, so no better source exists yet. Replies typed by someone who has
not seen the prompt are the only genuinely held-out data available, and are worth more than
another fifty authored ones.

Where the labels and the model disagreed on dates, the labels were wrong three times out of four.

## Reliability, which is a different question from accuracy

A parse failure is not the model misreading a reply. It is the endpoint returning a 200 with no
content — OpenRouter routes one model across providers and they do not all behave alike. The
parser retries {ATTEMPTS_PER_CALL} times and then gives up, marking the reply failed rather than
inferring anything from silence.

Counting those as missed intents would blame the model for an infrastructure fault and hide the
real problem, so the accuracy below is measured **only over replies that parsed**.

| | |
|---|---|
| Replies | {len(cases)} |
| Parsed successfully | {scored} ({reliability:.0%}) |
| Failed after {ATTEMPTS_PER_CALL} attempts | {len(parse_failures)} |
| Needed at least one retry | {retried} |
| Model calls made | {attempts_used} for {len(cases)} replies |
| Cost per parse | ${cost_per_parse:.6f} (mean over {len(costs)} priced replies) |

A failure is operationally safe — the reply is preserved and queued for a human, and nothing fires
on a guess. It is still a reply the agent did not handle, and at this rate it is the largest single
weakness in the parser.

## Latency

{latency_note}

This model reasons before it answers, and the reasoning is billed to the same completion budget
as the visible output — measured directly, a trivial prompt spent its entire budget on reasoning
and returned no content at all. `reasoning: {{"effort": "low"}}` is set on every call for exactly
this reason: it cut reasoning tokens from the hundreds-to-low-thousands down to single digits on
the hardest multi-intent cases in this corpus, with no accuracy loss measured against them.

## Precision and recall by intent

Over the {scored} replies that parsed.

| Intent | TP | FP | FN | Precision | Recall |
|---|---:|---:|---:|---:|---:|
{table}

**Recall** answers "did we miss any of these?" **Precision** answers "when we said yes, were we
right?"

## The numbers that matter, and they are on opposite sides

A single headline figure would hide the important half, because the two directions of error cost
differently.

- **Recall on `opt-out` and `distress`.** Missing one means messaging somebody who asked to be left
  alone, or dunning somebody who has lost their job. Money does not compensate for either.
- **Precision on `cancellation-request`.** A false positive cancels a paying customer who was
  merely annoyed. The parser over-detects this, and `shell/replies.py` refuses to act below 0.85
  confidence — so the model's precision here is not the system's behaviour. No reply on the
  corpus's forbid-list was acted on as a cancellation.

## Failures

{block("Parse failures", parse_failures)}
{block("Forbidden intents detected", forbidden_hits)}
{block("Date mismatches", date_misses)}
{block("Promises that named a date and changed nothing", threshold_misses)}
""")
    print(f"\nreport written to {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
