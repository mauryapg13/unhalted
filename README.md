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
- **Seven hard stops** — revocation, opt-out, dispute, distress, chargeback, merchant pause,
  regulatory hold — that no recommendation at any confidence can override.

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
  stopping_rules.feature       the seven hard stops
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

A configurable holdout — 10% by default — of recoverable cases receives baseline behaviour only:
one blind retry, no diagnosis, no contact.

**Reported lift is the agent cohort minus the holdout.** Gross recovery is never reported as lift.
Cases that turn out to be false failures, where the debit had actually succeeded, are reported
separately and never counted as recoveries.

## Results

Measurements land here once the batch has run. Nothing is claimed until it is measured.

| Metric | Value |
|---|---|
| Cases processed | — |
| ₹ at risk | — |
| Agent cohort recovery rate | — |
| Holdout recovery rate | — |
| **Net lift** | — |
| Rules-table share of diagnoses | — |
| Opt-out recall | — |

## Running it

```bash
uv sync --all-extras
uv run pytest
```

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
