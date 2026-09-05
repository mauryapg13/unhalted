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

One qualification on that table, since it is the project's central claim: of the model's three jobs,
**diagnosis and reply parsing are wired into the live path and drafting is not**. `core/draft.py`
writes a message and `shell/lint.py` blocks it if it invents a discount — both real, both tested —
but every message this system actually sends is one of the three plain, hand-written bodies in
`shell/notify.py`. Wiring the drafted one in is a decision about spending a model call per nudge
that has not been made, not a gap nobody noticed.

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

## What every documented failure actually does

The whole routing table, generated from the code rather than described. Every one of these resolves
with no model call.

**The source can outrank the reason.** Checked before anything else, because for these the reason is
incidental:

| `error_source` | Result | Why |
|---|---|---|
| `business` | held for a person, no retry, no contact | Razorpay: *"Business failures require corrective action rather than retries… simply retrying the same request won't resolve them."* The merchant's own configuration is wrong; the customer cannot fix it and a retry fails identically |

**Where the source decides between two documented causes.** These are the cases the model exists
for, resolved deterministically when Razorpay names a source and held when they do not:

| `error_reason` | `error_source` | Diagnosis | Confidence | Entry rung |
|---|---|---|---|---|
| `payment_timed_out` | `customer` | notification-gap | 1.0 | nudge |
| `payment_timed_out` | `bank` | recoverable-technical | 1.0 | silent retry |
| `payment_timed_out` | *unstated* | recoverable-technical | 0.8 | silent retry |
| `credit_failed` | `customer` | mandate-state-broken | 1.0 | re-authorisation |
| `credit_failed` | *unstated* | recoverable-technical | **0.4** | *held — below threshold* |
| `payment_failed` | `bank`, `issuer`, `issuer_bank` | recoverable-technical | 0.8 | silent retry |
| `payment_failed` | *unstated* | recoverable-technical | 0.8 | silent retry |

`credit_failed` without a source is the mechanism working: two documented causes, neither excluded,
so confidence is capped at 1/2 and falls below the 0.70 threshold — it holds for a person rather
than guessing which one it was.

**Everything else**, where the reason alone settles it:

| Entry rung | `error_reason` | Confidence |
|---|---|---|
| **nudge** — contact, with a payable link | `insufficient_fund`, `insufficient_funds` | 1.0 |
| | `payment_collect_request_expired` | 1.0 |
| | `authentication_failed`, `payment_declined`, `transaction_limit_exceeded` | 0.8 |
| **re-authorisation** | `bank_account_invalid`, `card_disabled_for_online_payments`, `card_expired`, `card_not_enrolled`, `debit_instrument_blocked`, `debit_instrument_inactive`, `incorrect_ifsc`, `invalid_vpa`, `mandate_not_active`, `transaction_on_vpa_restricted`, `vpa_resolution_failed` | 1.0 |
| | `incorrect_cvv` | 0.8 |
| **silent retry** | `bank_technical_error`, `gateway_technical_error`, `payment_mandate_not_active`, `server_error` | 1.0 |
| | `bank_account_validation_failed`, `card_declined` | 0.8 |
| **held, deliberately** | `payment_risk_check_failed` (a bank calling it fraud), `input_validation_failed`, `invalid_amount`, `international_transaction_not_allowed`, `invalid_currency` | 0.0 |
| **held, nothing to recover** | `payment_cancelled` | 0.4 |
| **held, unrecognised** | anything absent from the table | 0.0 |

Two limits stated rather than buried: **re-authorisation has no executor on this deployment** — it
records the absence and routes to a person instead of pretending — and **silent retry reaches
`execute_retry`, which has no debit adapter** for the same reason. See [Honesty about the
data](#honesty-about-the-data).

An empty account is the one class that does not begin by retrying. `insufficient_fund` enters at
**nudge** and asks the customer when to try again, because whether a retry works depends on when
they will have money and no API reports that — their answer reschedules the debit, and a fallback
retry runs only if nobody replies.

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
- **Contact hours**, 08:00-19:00 IST, on every channel and every kind of message.
- **A contact ceiling of one message per customer per fortnight**, counted across every case they
  have and every channel. The retry cap bounds debits and says nothing about messages: a customer
  with four failing subscriptions spends no retries at all and, without this, hears from us four
  times in four days. A message over the budget is deferred, never dropped. The window is
  configuration, not a constant — `contact.window` in `config/policy.yaml`.
- **Nine hard stops** — revocation, opt-out, dispute, distress, retry cap, ladder exhaustion,
  chargeback, merchant pause and regulatory hold — that no recommendation at any confidence can
  override.
- **A stop stays in force.** Eight of the nine bar contact, and that bar is stored at the rule's
  own scope rather than expiring with the queue it emptied. A customer who opts out is not
  re-entered into automation by anything they later say — not a date, not "actually, continue",
  not a payment. `unhalted stops` lists what is in force; `unhalted lift-stop <id> --by <name>`
  is the only way back, and it records the name.

A merchant cannot pause a UPI mandate, only cancel it, so "pause recovery" always means pausing
this program and never the mandate itself.

## Closing the loop

A nudge nobody can prove was paid, and a nudge that genuinely went unanswered, look identical to a
system that doesn't tag its own links. `shell/paylink.py` tags every recovery link with
`reference_id=case.id` — a documented Razorpay Payment Links field — and `ingest/webhooks.py`
handles the real `payment_link.paid` event that comes back: the case closes as `RECOVERED`, every
pending retry and nudge is cancelled, and the real payment id and amount land in the audit trail.
The customer is told so — a plain confirmation through the same notifier and the same
contact-hours gate a nudge uses, not silence.

None of that runs on its own. `unhalted run-due` (also reachable over HTTP, at
`/internal/run-due`, for a deployment with no long-running process of its own) is what executes a
scheduled action against a durable, leased queue: a worker that dies mid-action does not strand it,
a lease claims a row so two workers cannot take the same one, and a cancellation reaching a case
between the claim and the execution always wins.

## Closing gaps, proposed not applied

Two things in this project change without a person editing code by hand, and both stop one step
short of writing the file themselves:

- **`config/policy.yaml`** is the one place every threshold this system enforces lives — NPCI
  bands, contact hours, the retry cap, ladder costs, confidence thresholds.
  `scripts/propose_policy_change.py` reads free text describing a regulatory change and proposes a
  field-level diff, each value checked against the exact words in the source that justify it. It
  has no filesystem-write call in it at all — a test inspects its own source to confirm that.
- **`core/taxonomy.py`** is the rules table diagnosis resolves against. A case it cannot match sits
  `UNKNOWN`, held for a human. `scripts/propose_taxonomy_rule.py` clusters those held cases by their
  exact failure shape — method, error reason, source, step — and proposes a rule from documentation
  supplied by the caller, the same evidence-quote discipline. The first live test of this caught
  the model marking a rule `DIRECT` while its own stated reasoning said the cause "is not
  communicated" — recorded in [`BREAKAGE.md`](BREAKAGE.md), and the reason neither proposer is
  trusted to write its own answer in.

## The specification, and what checks it

The behaviour is specified in Gherkin. The feature files are the contract this project is built
against, and the pytest suite is what executes against them — the constraints they describe are
tested in `tests/`, scenario by scenario, by hand-written tests rather than by generated step
definitions. `tests/test_specification.py` holds the feature files themselves to their shape.

That seam is worth stating plainly, because a scenario can be written here and never bound to a
test: the contact ceiling was specified below, listed above as a hard rule, and implemented
nowhere until a behavioural audit went looking for it. `tests/test_contact_ceiling.py` exists
because reading the Gherkin was not enough to know.

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

**Real.** Mandate registration, token state, webhooks and signatures, and payment objects with their
`error_reason` values all run against Razorpay test mode. Failed
payments are produced through a real hosted checkout, with a real signature-verified webhook
arriving at a real endpoint. What they do **not** yield is a specific documented error reason —
tested twice, on both checkout surfaces available to this account, Razorpay's error-scenario test
cards return the generic `payment_failed` / `gateway` regardless of which card is used (issue #8,
closed as answered). The taxonomy's other 15 documented card reasons are mapped and unit-tested
against Razorpay's published definitions; only this one has travelled the full chain end to end.

**Replayed.** Batch volume. Razorpay test mode cannot produce an `insufficient_funds` decline on a
UPI Autopay debit for anyone, and 500 real checkout interactions are not feasible by hand. The
batch replays captured payloads across Razorpay's published UPI error taxonomy with varied
identifiers, amounts and timings. Every error reason and field shape came off Razorpay's
infrastructure; the volume did not.

**Implemented but not exercisable.** NPCI window rejection. The shell refuses out-of-window retries
before it ever calls Razorpay, so the refusal is real logic — but test mode cannot produce a live
NPCI rejection to compare it against.

**Built, and deliberately not wired in.** Three things, each real code with real tests, none of them
reached by the live path — listed here rather than left for a reader to discover:

- **Message drafting.** `core/draft.py` and its compliance lint. Every message that actually goes out
  is one of the plain hand-written bodies in `shell/notify.py`; see the note under [the
  thesis](#the-thesis).
- **Payment-status verification.** `shell/verify.py`'s `RazorpayVerifier` asks Razorpay whether an
  order was since paid by another attempt, which is the check that stops a customer being debited
  twice. Nothing constructs one, so `handle_failure` runs with no verifier — and the behaviour when
  none is configured is the safe one: the case is **held for a person** rather than assumed unpaid,
  because "could not check" is not "not paid". Wiring it needs a live client on the ingest path.
- **The upper ladder.** Re-authorisation, voice call and human callback have no executor, as does the
  debit adapter behind every retry. Each refuses out loud and routes to a person.

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
| Debit attempts scheduled | 84 | 900 | 816 fewer |
| Attempts a retry provably cannot fix | 0 | 108 | 108 avoided |
| Attempts inside NPCI restricted bands | 0 | 117 | 117 avoided, UPI only |
| Customer contacts scheduled | 189 | 0 | the baseline never contacts anyone |
| Cases held for a human | 27 | 0 | the baseline has no such path |
| Intervention spend | ₹225 | ₹0 | messages are not free, and this is what they cost |
| Diagnoses requiring a model call | **0 of 300** | — | inference spend ₹0.00 |

**Read those first two rows together.** Attempts fell from 217 to 84 and contacts rose from 56 to
189 in the same change, and the trade is the point rather than a side effect: an empty account no
longer gets three silent retries guessing at a payday, it gets one message asking when to try. The
guess cost three debit attempts and told the customer nothing; the question costs ₹1 and produces
the one fact — when money arrives — that decides whether any retry works at all. A system that
contacted nobody and spent 217 attempts was cheaper on the wrong axis.

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
uv run unhalted run-due               # execute whatever has come due, once — safe to run twice
uv run unhalted breakeven             # what the money argument rests on
uv run unhalted calibration           # whether confidence predicts outcome, on real cases only
uv run unhalted report                # the batch measurement, glanced at rather than read
uv run unhalted capabilities          # what this deployment can actually do
uv run unhalted policy                # the currently loaded policy — every threshold enforced
```

A case is not driven by the CLI alone. `scripts/inject.py` runs one of Razorpay's documented error
scenarios through the real pipeline without waiting on a webhook; `scripts/session.py` does the
same against a real captured payment, with you typing the customer's replies; `scripts/schedule.py`
is the live view of the durable queue — scheduled, due, executed, recovered, as each happens; and
`scripts/review.py` is where a held case actually gets approved, rejected, or reclassified by a
person.

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
  agent.py   the control loop — the only place that calls the model
  runner.py  the durable queue's executor — leased, at-least-once, idempotent
  store.py   SQLite: an append-only audit trail beside the pending-action queue
  cli.py     unhalted <command> — the observability surface
  policy.py  reads config/policy.yaml — every threshold, in one file
  ingest/    webhook verification, payload normalisation, case creation
  core/      diagnosis, reply parsing, drafting, proposing     (the model lives here)
  shell/     windows, caps, stops, ladder, lint, pay links     (nothing here calls a model)
  measure/   batch generation, holdout, comparison, calibration, reporting
scripts/
  inject.py, session.py     drive a real case without waiting on a webhook
  schedule.py, review.py    the live queue, and the human queue behind it
  propose_*.py              a policy or taxonomy change, proposed from free text
tests/
  features/  the specification, executed
```

## What broke

Development failures and what they changed are recorded in [`BREAKAGE.md`](BREAKAGE.md).
