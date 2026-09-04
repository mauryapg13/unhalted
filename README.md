# unhalted

**A mandate-recovery agent for Razorpay subscriptions. The model diagnoses; a deterministic shell decides.**

Razorpay's subscription lifecycle is `charged → pending → halted`. When a mandate debit fails,
the subscription moves to `pending`, blind retries run, and when they are exhausted it moves to
`halted`. Nothing in that window asks *why* the debit failed.

UPI Autopay revocations are running at roughly 20 million per month, largely driven by low
customer balances. The gap between `pending` and `halted` is where that money dies.

`unhalted` lives in that gap.

---

## The thesis

Every recovery system built on an LLM has to answer one question: what is the model allowed to do?

Here the answer is narrow and enforced in code.

| | AI core | Deterministic shell |
|---|---|---|
| **Owns** | perception and drafting | action and stopping |
| **Does** | diagnose failures, parse replies, draft messages | schedule, send, escalate, halt |
| **Outputs** | a class, a confidence, a draft | money movement, customer contact |
| **Can be wrong** | yes, and it is caught | no, it is the thing that catches |

The model never picks an action. It returns a **scored recommendation**, and every recommendation
crosses a gate before it becomes an action. The gate is ordinary code with ordinary tests.

## Why a model is needed at all

Razorpay's own UPI error documentation records that several error codes have multiple distinct
root causes:

| Error code | Root cause A | Root cause B |
|---|---|---|
| `payment_timed_out` | customer exceeded the 10-minute limit | partner bank downtime |
| `gateway_technical_error` | partner bank technical issue | partner bank downtime |
| `credit_failed` | customer used a bank account other than the one registered | partner bank downtime |

These demand opposite recovery paths. A customer timeout wants a re-notification and a retry with
the customer in the loop. Bank downtime wants a silent retry later and *no customer contact at
all* — messaging them would be noise about a problem that is not theirs.

The code alone cannot choose. Disambiguating it from the surrounding signals is the job the model
is given.

## Where a model is deliberately *not* used

Most failures are unambiguous. `insufficient_funds` means what it says. Those are resolved by a
lookup table over Razorpay's real `error_reason` values, with no model call, and the diagnosis is
logged with `source: rules-table` so the split is auditable rather than asserted.

Timing, caps, stops, spend limits and compliance checks are likewise not model decisions. They are
regulation and policy, and they live in `src/unhalted/shell/` where they can be tested.

## Rules the shell enforces

Sourced from NPCI and Razorpay's recurring-payments documentation, not invented:

- **NPCI execution windows.** UPI Autopay may execute before 10:00, between 13:00–17:00, and after
  21:30 IST. The bands 10:00–13:00 and 17:00–21:30 are restricted.
- **25-hour pre-debit rule.** Subsequent charges are initiated 25 hours after the pre-debit
  notification is sent to the customer.
- **₹15,000 per-transaction ceiling** on UPI Autopay. Above it, additional customer authorisation
  is required, so the case routes differently rather than retrying.
- **The mandate's own `max_amount`.** Never attempt a debit above the registered ceiling.
- **Retry cap of 3** per billing cycle, matching NPCI's one-execution-plus-three-retries allowance.
- **Contact hours and a shared weekly contact ceiling** across every channel.
- **Nine hard stops** — revocation, opt-out, dispute, distress, retry cap, ladder exhaustion,
  chargeback, merchant pause and regulatory hold — that no recommendation at any confidence can
  override.

A merchant cannot pause a UPI mandate, only cancel it, so "pause recovery" always means pausing
this program and never the mandate itself.

## Specification as test suite

The behaviour is specified in Gherkin and executed as tests. Every regulatory constraint and every
stopping rule in [`tests/features/`](tests/features/) is a check that goes red if the shell weakens.

```
tests/features/
  diagnosis.feature            failure classification and confidence gating
  retry_orchestration.feature  NPCI windows, preconditions, retry caps
  reply_understanding.feature  intent extraction, precedence, entity validation
  escalation_ladder.feature    intervention selection, EV gates, compliance lint
  stopping_rules.feature       the nine hard stops
  audit_measurement.feature    decision records, replay, holdout measurement
  human_gates.feature          degradation behaviour and human review
```

The full annotated specification is in [`docs/spec.md`](docs/spec.md).

## Honesty about the data

This matters more than the numbers, so it is stated up front rather than in a footnote.

**Real.** Mandate registration, token state, webhooks and signatures, payment objects and their
`error_reason` values, and payment-status verification all run against Razorpay test mode. Failed
payments are produced with Razorpay's error-scenario test cards, which yield specific documented
error reasons rather than generic failures. Mandate-state failures — expiry, `max_amount` breach,
revocation — are triggered against real mandates.

**Replayed.** Batch volume. Razorpay test mode cannot produce an `insufficient_funds` decline on a
UPI Autopay debit for anyone, and 500 real checkout interactions are not feasible by hand. The
batch replays captured payloads across Razorpay's published UPI error taxonomy with varied
identifiers, amounts and timings. Every error reason and field shape came off Razorpay's
infrastructure; the volume did not.

**Implemented but not exercisable.** NPCI window rejection. The shell refuses out-of-window retries
before it ever calls Razorpay, so the refusal is real logic — but test mode cannot produce a live
NPCI rejection to compare it against.

## Measurement

The control is **Razorpay's own documented behaviour** — three automatic retries on consecutive
days, no diagnosis, no contact, then `halted`. Not a strawman: it is what their subscription
documentation says happens today.

A holdout of 10% is assigned before any policy sees a case, and on generated data **it does no
work** — a control group exists to absorb unobserved variation, and two deterministic policies
over a generated batch have none. Both policies are run over *every* case instead, which is a
paired comparison and strictly stronger. The holdout stays for the day this runs against real
traffic.

**Rupees recovered is not reported as a measurement**, here or anywhere in this repo. Deciding
whether a given retry succeeded means writing an outcome model, and whoever writes it decides the
answer. What is countable without one — attempts spent, attempts that provably cannot work,
windows violated, customers contacted, cases held — is reported as fact; recovery is reported as a
sensitivity range with its assumptions on the page.

## Results

300 generated failures drawn from Razorpay's documented error taxonomy — ₹1,20,850 at risk — run
through both policies. Full report: [`docs/batch-measurement.md`](docs/batch-measurement.md),
regenerated with `uv run python scripts/run_batch.py`.

**Counted.** Nothing below needs to know whether anything recovered.

| | Agent | Razorpay's documented retry | |
|---|---:|---:|---|
| Debit attempts scheduled | 217 | 900 | 683 fewer |
| Attempts a retry provably cannot fix | 0 | 108 | 108 avoided |
| Attempts inside NPCI restricted bands | 0 | 117 | 117 avoided, UPI only |
| Customer contacts | 56 | 0 | the baseline never contacts anyone |
| Cases held for a human | 27 | 0 | the baseline has no such path |
| Diagnoses requiring a model call | **0 of 300** | — | inference spend ₹0.00 |

NPCI's execution bands govern **UPI Autopay** and nothing else, so only the UPI share of the
baseline's attempts is counted against them. An earlier version of this table said 315 by applying
the rule to cards and emandate too, which credited this system with an advantage it does not have.

That last row is the architecture appearing as a count rather than a claim: every failure in
Razorpay's documented taxonomy resolved deterministically, so inference cost nothing because
nothing needed inferring.

**Where the model is not optional.** 68 Hinglish and English replies through `z-ai/glm-5.3-flash`
([`docs/reply-evaluation.md`](docs/reply-evaluation.md)):

| | |
|---|---|
| Parsed successfully | 68 of 68 |
| `opt-out` recall | 1.00 |
| `distress` recall | 1.00 |
| `promise-to-pay` recall | 1.00 |
| `cancellation-request` precision | 0.62 |
| Cost per parse | $0.00007 (OpenRouter `usage.cost`) |
| Latency | median 1.0s, up to ~11s on a slow call |

Recall is 1.00 on every intent where missing one causes harm. Precision is where it is weak, and
the shell is what makes that survivable: cancellation needs 0.85 confidence before anything acts,
and no reply in the corpus cleared it.

Every call runs at `reasoning: {"effort": "low"}` — this model reasons before answering, billed to
the same completion budget as the visible output, and a trivial prompt at default effort spent its
entire budget on invisible reasoning tokens with nothing left to answer with. Lowering it cut
reasoning tokens to single digits and latency 3–9x on the hardest cases in this corpus, with no
accuracy loss measured against them. See [`docs/reply-evaluation.md`](docs/reply-evaluation.md).

**Not reported as measured:** rupees recovered. See [Measurement](#measurement).

## Running it

```bash
uv sync --all-extras
uv run pytest
```

Read what the agent did:

```bash
uv run unhalted cases                 # what is open, held, closed
uv run unhalted case CASE-8EF53CCD    # one case, end to end
uv run unhalted compare CASE-8EF53CCD # the same case under Razorpay's retry policy
uv run unhalted queue                 # what is waiting on a person
uv run unhalted breakeven             # what the money argument rests on
uv run unhalted report                # the batch measurement
uv run unhalted capabilities          # what this deployment can actually do
```

`compare` is the one to run first. A single case shows no contrast on its own — the agent declines
a futile retry, and a reader with nothing to compare it against sees a system doing nothing. Put
Razorpay's documented policy beside it and the same case reads differently:

```
                     Razorpay's retry policy                     unhalted
  03 Sep 17:30                                                   mandate-state-broken
  03 Sep 17:30                                                   entering at rung 3: re-authorisation link
  04 Sep 17:30     | DEBIT ATTEMPT 1/3  cannot work · NPCI band
  05 Sep 17:30     | DEBIT ATTEMPT 2/3  cannot work · NPCI band
  06 Sep 17:30     | DEBIT ATTEMPT 3/3  cannot work · NPCI band
  06 Sep 17:30       HALTED — no diagnosis, no contact, no memory

                                     agent  baseline
    Debit attempts                       0         3   3 fewer
    Attempts a retry cannot fix          0         3   3 avoided
    Attempts inside NPCI bands           0         3   3 avoided
```

The left column is `measure/baseline.py` replaying Razorpay's documentation. The right is the audit
trail. Neither says what was recovered — that needs an outcome model, and whoever writes one
decides the comparison.

See a failed payment go through end to end — a forged webhook rejected, a real one accepted,
a redelivery recognised, and the resulting case timeline:

```bash
./scripts/demo.sh
```

It posts to the same endpoints Razorpay posts to, with a real HMAC signature, and reads the case
back through the same API anyone else would use. The NPCI window rule reads the real clock, so
inside a restricted band (10:00-13:00 or 17:00-21:30 IST) you will watch the shell move the retry
and log the violation.

```bash
cp .env.example .env
uv run uvicorn unhalted.ingest.webhooks:app --reload
```

## Layout

```
src/unhalted/
  ingest/    webhook verification, payload normalisation, case creation
  core/      diagnosis, reply parsing, drafting        (the model lives here)
  shell/     windows, caps, stops, lint, validation    (nothing here calls a model)
  measure/   batch generation, holdout, reporting
tests/
  features/  the specification, executed
```

## What broke

Development failures and what they changed are recorded in [`BREAKAGE.md`](BREAKAGE.md).
