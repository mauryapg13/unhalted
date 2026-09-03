# Changelog

## [Unreleased]

### Added
- A runner. `pending_actions` had three writers and nothing that executed one, so a retry scheduled
  for 13:00 was a row recording an intention and 13:00 arrived and nothing happened. `unhalted
  run-due` and `POST /internal/run-due` now execute what has come due — the same function behind a
  command, an HTTP request an external scheduler can post to, or a person typing it, so the
  deployment shape stays an open question.
- Actions are claimed under a **lease**, not a lock. Claim and read are one transaction, because
  selecting first and updating after is how the same retry reaches two workers. A lease expires, so
  a worker that dies mid-action does not strand its rows — they return to `pending` and are tried
  again. Delivery is at-least-once and the executors have to be safe under repetition, which is the
  discipline the ingest side already applies to Razorpay's redelivery.
- Cancellation reaches leased rows and the runner re-reads state immediately before it acts, so a
  customer revoking while a worker holds the lease still stops the charge.
- Every execution is written to the audit trail beside the decision that caused it, carrying the
  worker's name — a decision recorded without its execution is half an account.
- **The debit adapter is absent, and says so.** Initiating a charge needs a live mandate token and
  this account cannot register one for UPI. `retry` refuses, records why, and routes the case to a
  person rather than reporting a success nobody performed. See #31.

### Fixed
- `run_due` treated an empty executor mapping as a request for the defaults, because `executors or
  EXECUTORS` cannot tell "none registered" from "none supplied". Found by the test for it.

### Fixed
- NPCI's execution bands are applied only to the rail they govern. `windows.py` opened by saying the
  restriction is on **UPI Autopay** while the code applied it to every method, so card retries were
  delayed for a regulation that does not reach them — and, worse, `WINDOW_VIOLATION` was written to
  the audit trail for violations that never happened. The audit trail is the one account of events
  this project asks anyone to trust; a rule recorded as fired when it did not apply is the kind of
  thing that discredits everything beside it. `is_execution_allowed` and `next_allowed_execution`
  now take a method, `schedule_retry` threads it through, and an unknown method keeps the
  conservative reading: a card delayed wrongly costs hours, a UPI debit inside a band is a breach.
  Closes #30.

### Added
- The drift check against Razorpay's documentation can be run on demand, via `workflow_dispatch`,
  as well as on every push and pull request. That is the trigger that matches how the rule actually
  changes: somebody reads that a circular has landed and comes to check.
- An unattended daily schedule sits in `ci.yml`, written and commented out, with the conditions for
  switching it on. It is the right trigger for a running service and the wrong one for a repository
  that will be quiet between bursts of work — GitHub disables scheduled workflows after sixty days
  of inactivity, and a daily run nobody watches decays into a red badge that means nothing. Recorded
  as a decision rather than left as a gap.

### Added
- `unhalted breakeven` and `measure/outcomes.py` — the money argument, with nothing in it that is a
  forecast. Money is sorted by what each policy can *reach* using Razorpay's own error descriptions;
  the ceilings on each policy are stated as ceilings; and the intervention's real cost is divided by
  the money it is placed in front of to give the conversion rate below which it loses money.
- On a mandate-heavy book of 400 cases that rate is **0.389%** — under half of one customer in 116
  has to re-authorise before a ₹232 campaign has paid for itself, against ₹59,624 of exposure. The
  useful property is that the unknown becomes the answer instead of an input.
- The report names what it will not say. Rupees recovered needs real outcomes at volume; supply a
  conversion rate and the figure is a multiplication, but the rate is then the merchant's and is
  labelled as theirs. A published benchmark from another market is refused explicitly: card-updater
  recovery in the US is a different mechanism from a re-authorisation needing an MPIN.

### Fixed
- The baseline is three retry models, not one. Razorpay documents UPI, cards and emandate as
  separate tabs and they do not agree: UPI is T+1/T+2/T+3 then halted, cards are "thrice, once
  every day for 3 days", and emandate states **no retry count at all** — the next attempt waits on
  the previous one settling, "as it may take more than 24 hours", with bank-holiday shifting to T-1
  or T-3. Applying the UPI model to all three was an assumption nobody had checked. The emandate
  count is now declared as assumed via `assumption_used`, at the fastest interval their wording
  permits, which is the reading that cannot inflate the agent's advantage.
- NPCI's execution bands are counted only where they apply. They govern **UPI Autopay**;
  `windows.py` said exactly that in its own docstring while the code applied them to every method,
  so the baseline was charged with violations on rails the rule does not reach. The 300-case batch's
  figure falls from 315 to 117, and a mandate-heavy 400-case mix from 705 to 213. The README and the
  batch report are corrected. The agent's own scheduler still applies the bands universally, which
  is conservative rather than correct — raised as #30 rather than changed here, because it needs to
  be right about three rails and one of them wants a bank-holiday calendar.

### Fixed
- The taxonomy now reads Razorpay's **recurring** error tables, not only `errors/payments`. Their
  emandate reference documents a "Subsequent Payments" section — a mandate debit failing after
  registration, which is the exact event this product exists for — and the generator had never
  opened it. Eight reasons had no rule, `mandate_not_active` among them. Money the system can
  defensibly claim on a mandate-heavy book went from 17% to 26% of everything at risk, and cases
  reaching a human fell from 29% to 14%. Closes #28.
- `payment_failed` no longer reaches full confidence from any source. It was marked `DIRECT`,
  meaning Razorpay's description states the class; their description says the opposite — "the exact
  reason in this case is not communicated to Razorpay". It is the commonest reason on the account
  and two of its sources put it in the highest autonomy band. Also keyed on `issuer_bank`, the
  value their emandate reference actually emits, where we had guessed `issuer`. Closes #29.
- Confidence no longer rises when the payment method is unknown. `documented_causes` fell through
  to the method-agnostic list, which records one cause for everything because it has no method to
  attribute to — so an unknown method scored 0.8 and auto-executed where UPI scored 0.4 and held.
  Unknown and unmapped methods now take the worst documented count across the methods Razorpay does
  publish a table for. Closes #23.
- Replies carrying several intents parsed one time in three. The completion budget ran out before
  the model emitted anything, and `finish_reason: "length"` — present on every such response, read
  by nothing — was the cause that `BREAKAGE.md` had attributed to provider routing. `max_tokens` is
  4000, a truncated response is reported as truncated, and it is no longer retried: at temperature 0
  it fails identically three times and bills for each. Measured over 90 live calls, multi-intent
  parses went 33% to 93% at half the cost per successful parse. Closes #22.
- Evidence spans are checked against the reply. They were documented as required and defaulted to
  the empty string, so a reviewer could be shown words the customer never wrote with the same
  standing as a quote. Closes #25.
- Payments in a currency other than INR are refused at ingest instead of being read as rupees.
  Razorpay accepts non-INR orders; a USD one was created on this account while testing, and $499
  flowed through the ceilings and the expected-value gate as Rs 499. Closes #24.
- Inference spend is recorded. `usage.cost` was read in one health check and nowhere in the product,
  so the report's spend line was a constant. It is now carried on `ParsedReply` and written to the
  audit record — including for calls that returned nothing, which are billed too. Closes #26.
- A whole-call timeout. `TIMEOUT_SECONDS` was a per-operation read timeout, and one call during
  testing held a worker for 611 seconds under a nominal 45.

### Fixed
- Recorded, not yet fixed: an exploratory pass against the live endpoint and the real Razorpay
  account found five defects the suite does not cover — reply truncation at `max_tokens` (#22),
  confidence rising when the payment method is unknown (#23), currency recorded and never read
  (#24), unvalidated evidence spans (#25), and inference spend that no code path can report (#26).
  `BREAKAGE.md` carries the first, which also corrects an earlier entry's conclusion.

### Added
- `unhalted compare <id>` — the same case under both policies, side by side. The agent's column is
  read off the audit trail and Razorpay's is `measure/baseline.py` replaying their documented
  T+1/T+2/T+3 behaviour, both anchored to the moment the failure arrived. On an expired card the
  baseline spends three debits that provably cannot work, all three inside an NPCI restricted band,
  and the agent spends none.
- Neither column claims a recovery. The comparison stops where the batch report's part one stops,
  for the same reason: an outcome model decides the comparison, so there isn't one.

### Fixed
- The customer terminal, the reviewer terminal and the CLI now read one database. Two of them
  hardcoded `session.db` while the CLI defaulted to `unhalted.db`, so a reviewer could open the
  queue and correctly see nothing while a case sat held in the other file. All three take
  `UNHALTED_DB` now.

### Changed
- The batch report counts **model calls**, not only inference spend. `Rs 0.00` beside no other
  figure reads as unmeasured; `0 of 300 diagnoses required a model call` reads as the measurement
  it is, and it is the 85% claim appearing as a count. The report also states that the figure
  covers diagnosis alone, since a generated batch has no customer replies to parse, and gives the
  measured per-parse cost so nobody concludes the model is free. Closes #16.

- The report explains why no case was closed as uneconomic instead of leaving a bare zero. The
  provable half of the expected-value gate cannot fire at an entry rung — the dearest entry is
  re-authorisation at ₹2 against a cheapest stake of ₹49 — and becomes reachable only on
  escalation, which this batch cannot simulate without the outcome model the whole report refuses
  to write. The gate is exercised by its own tests, not by the batch, and the demo no longer leans
  on it. Closes #15.

- The README's results table carries measured numbers, and its measurement section no longer
  promises a lift figure derived from the holdout. Rupees recovered is stated as modelled
  everywhere it appears. Closes #10.

- The confidence thresholds in `Diagnosis.authority` now say at the point of definition that they
  are policy rather than measurement. `0.90` and `0.70` came from the specification and nobody
  measured them. Generated data cannot settle them — the correct class is whatever was generated —
  so what is reported instead is how much the choice matters: 68% of cases land above 0.90, 23%
  between, 9% below. Closes #7.

- C6 (reply understanding) joins C5 and the C8 holdout in the never-cut set. With 85% of documented
  failures resolving deterministically, reply parsing is where a model is irreplaceable, so cutting
  it would remove the answer to what the AI does. The diagnosis model seam is recorded against C6,
  where the plumbing is written anyway, and human-queue preparation against C7.

### Added
- `unhalted` on the command line: `case` prints one case end to end with the rules that fired and
  the taxonomy version that produced the diagnosis, `cases` and `queue` list what is open and what
  is waiting on a person, `report` prints the batch measurement, and `capabilities` reports what
  this deployment cannot do as well as what it can.
- Decisions are logged as they happen, not only reconstructable afterwards. Every decision passes
  through one place, so that is where the log line is written.

### Fixed
- A scheduled retry is now recorded as pending work rather than only as a line in the audit trail.
  It was not cancellable, so a customer who revoked their mandate would have been charged anyway.
  Found by the CLI printing `pending 0` beside a scheduled retry on its first real use.

- Batch measurement. 300 generated failures drawn from Razorpay's published error taxonomy, run
  through both the agent and Razorpay's own documented behaviour — three automatic retries on
  consecutive days, no diagnosis, no contact. The report is split so a reader cannot confuse the
  two halves: what each policy *does* is counted and needs no assumption, and the rupee figure is
  modelled and shown as a range across success rates with the rates printed beside it.
- The counted half over 300 cases: the baseline schedules 900 debit attempts to the agent's 217,
  spends 108 of them on failures a retry provably cannot fix where the agent spends none, and
  lands 315 inside NPCI's restricted bands where the agent lands none.
- The confidence-band distribution, which is as far as issue #7 can be answered here: 9% of cases
  fall below 0.70, so the threshold governs about one case in eleven rather than the system.

- The escalation ladder, entered at the rung the diagnosis warrants rather than always at the
  bottom. A broken mandate skips silent retries entirely, because no number of them fixes an
  expired card and the attempts would spend NPCI's allowance proving what is already known.
- An expected-value gate split by what it can honestly claim. The success rates it uses for the marginal half are merchant policy rather than estimates anyone made — this project cannot measure them, and a decision resting on the conservative default records it as unmeasured.
  A rung costing more than the whole amount at stake is refused with no assumption at all — a
  probability cannot exceed 1.
- Compliance lint. This project offers nothing, so any offer in a draft was invented: offer
  language and **any percentage** are blocked, along with threats, manufactured urgency and
  commitments the agent cannot make. A message must carry the amount, the merchant, and a way to
  stop.
- Drafting with one correction. A blocked draft is regenerated once with the violation quoted
  back; a model that invents twice gets no third chance and the plain fallback is sent instead.
- Human-queue briefing. The model reads a held case and states what it thinks with what it
  weighed, so a reviewer decides in thirty seconds rather than two minutes. It is labelled as the
  agent's opinion, and a reviewer without one still has the full record.

- Diagnosis can now decline to decide. Where Razorpay documents that a failure might not be one —
  a `payment.failed` followed by a capture on the same transaction, when the customer retries in
  their own UPI app — the agent verifies whether the order was already paid before scheduling
  anything. A paid order closes as a false failure and is reported separately from recoveries,
  because that money was never lost. A check that cannot be performed holds the case: not-checked
  is not the same as not-paid, and assuming otherwise is how somebody gets charged twice.

- Reply understanding. `core/reply.py` reads free text into a closed set of intents with an
  evidence span quoting the words that justify each, and `shell/replies.py` decides what that
  changes — precedence and thresholds live there because both are policy. The thresholds are
  deliberately asymmetric: 0.50 for protective intents, 0.70 for anything that moves money, 0.85
  before acting on a cancellation, because missing an opt-out and inventing a cancellation cost
  very different things.
- `agent.handle_reply` takes a reply from words to consequence and records all of it. Nothing
  automated may remain scheduled on a case a person now owns — a customer who asks to cancel must
  not be charged while somebody actions it.
- A labelled corpus of 68 replies, 13 verbatim from the specification, with
  `scripts/evaluate_replies.py` reporting precision and recall per intent **including the
  failures**, and separating reliability from accuracy so an endpoint fault is not read as the
  model misreading.
- `scripts/session.py` and `scripts/review.py`: a terminal for the customer and a terminal for the
  reviewer, sharing a database. Held cases reach the queue, nothing expires into a yes, and every
  human decision is attributed by name.
- `shell/notify.py`, the notifier seam. Contact hours gate above the transport, so a console and a
  phone are governed identically.

### Fixed
- Disputes and chargebacks now hold the case for a human. Halting without holding abandons the
  case silently: the customer's claim about their money never gets answered.
- Cases are durable before anything is done with them. The store uses write-ahead logging, commits
  fsync before returning, and the webhook persists the signal before diagnosis — Razorpay retries
  anything slow, and a process that dies mid-diagnosis should not depend on that retry arriving.

- A third real captured payment, and the finding that produced it: Razorpay's error-scenario cards
  do not yield their documented reasons on this account through either checkout surface. Tested on
  the hosted payment-link page and on Standard Checkout, the widget the cards are documented for.
  Both return a generic `payment_failed` / `gateway`. The card taxonomy therefore remains
  capability complete and verification narrow, which `PROVENANCE.md` states plainly.

- The nine stop rules from the specification, each with its code, scope, SLA and a stated reason
  for existing. A stop cancels every pending action in scope inside one transaction, so a
  revocation arriving while a retry, two nudges and a voice callback are pending cancels all four
  together with no partial execution possible.
- Monetary ceilings with the consequences Razorpay documents, which differ by method: a card above
  ₹15,000 *fails*, so attempting it wastes an NPCI retry, while UPI above the frictionless limit
  *waits for the customer to authorise that debit* — a different recovery path, not a failure. The
  mandate's own `max_amount` is checked first, because consent outranks feasibility.
- Retry backoff per diagnosis class — thirty minutes then two then six hours for technical
  failures, a day for balance failures, twenty-five hours for a notification gap. Applied by the
  caller rather than inside `schedule_retry`, so a retry realigned to a date the customer named
  lands on that date instead of six hours after it.

### Fixed
- The monetary ceilings are now enforced in the agent's path rather than only in their own tests.
  `shell/limits.py` had twelve passing tests and no caller, so a debit above the card ceiling or
  above the mandate's own `max_amount` would have been scheduled anyway.

- Technical failures were scheduled for the same instant they failed, retrying into the same
  outage and burning one of the three attempts NPCI allows.

### Added
- Real captured payments in `tests/fixtures/razorpay/captured/`, each recording its payment id and
  capture date, with seven tests running the pipeline against them. `scripts/capture_fixtures.py`
  captures them and `docs/capturing-fixtures.md` documents the procedure.

### Fixed
- The service now loads `.env`. It never did — preflight parsed the file itself and the demo passed
  variables inline, so the only caller that mattered in production had no way to read its own
  webhook secret and refused every delivery Razorpay made.

### Added
- The diagnosis taxonomy's facts are now generated from Razorpay's error references and pinned to
  a commit of `razorpay/markdown-docs`, so `taxonomy_version` identifies the exact documentation
  a classification came from. `scripts/build_taxonomy.py` builds it and `--check` fails when their
  docs have moved; a CI job runs that check.
- Confidence is derived rather than chosen: the documented root-cause count caps it at `1/n`, a
  concrete `error_source` lifts the cap by selecting one cause, and a second factor records whether
  Razorpay's own description states the class or we inferred it. The audit reasoning names the
  causes it weighed, so it can be checked against their documentation.
- `method` joins the taxonomy key. Ambiguity is method-specific — `payment_timed_out` has one
  documented cause on cards and two on UPI — so the same reason yields 0.8 on a card and 0.4 on
  UPI, and only the second falls below the threshold for autonomous action.
- All 26 documented card and UPI error reasons are accounted for: 25 mapped to a recovery class,
  and `payment_risk_check_failed` deliberately held, because a bank calling a payment fraudulent
  has no appropriate automated response.
- Live API tests (`pytest -m live`) that hit the real Razorpay test-mode API and assert the field
  names and shapes the pipeline depends on. Excluded by default so CI needs no credentials; they
  refuse to run against anything but a test key. They caught, on their first run, that the
  Subscriptions API had become entitled after being 401 all morning.
- `scripts/demo.sh` drives a failed payment through the running service end to end: a forged
  webhook rejected, a real signed one accepted, a redelivery recognised, and the case timeline
  printed. It posts to the same endpoints Razorpay posts to rather than taking a demo-only path.
- A missing changelog entry now blocks rather than warns: a `commit-msg` hook locally, and a CI
  job on pull requests that `--no-verify` cannot bypass. Commits that genuinely need no entry
  say `[skip changelog]` in the message, in the open.
- Gherkin specification split into executable feature files under `tests/features/`, wired to
  `pytest-bdd` and run in CI.
- Project skeleton: packaging, CI workflow, README, licence, breakage log.
- Walking skeleton of the pipeline: normalised `FailureSignal`, SQLite case store with an
  append-only audit table, the diagnosis taxonomy keyed on Razorpay's `error_reason`,
  `error_source` and `error_step`, NPCI execution windows and contact hours, the retry
  scheduler, and the control loop that takes one failure from signal to scheduled action.
- Workflow enforcement: a git pre-commit hook refusing commits on `main`, a Claude Code
  `PreToolUse` guard, and `CLAUDE.md` stating the working agreement.
- Webhook ingest: `POST /webhooks/razorpay` verifying the `X-Razorpay-Signature` HMAC over the
  raw request body, and `GET /cases/{id}` returning a case timeline. A `payment.failed` event
  now runs end to end — case opened, diagnosed, retry scheduled inside a permitted NPCI window,
  every decision audited.
- Idempotent delivery, keyed on the `x-razorpay-event-id` header Razorpay prescribes. Redelivery
  is documented as expected behaviour, and a second case for one failure would count the same
  rupees twice.
- Two documented failure reasons added to the taxonomy: `payment_failed` from `bank` and from
  `issuer` at authorisation classify as recoverable-technical — a bank-side failure implies no
  customer action, so it is worth a silent retry and not worth a message.
- Test fixtures sourced from Razorpay's published webhook documentation, with provenance
  recorded. Real captures replace them at C4.
- Razorpay's official MCP server wired for development use, launched through a script that reads
  credentials from `.env` at run time and refuses to start on anything but a test key.

### Fixed
- The diagnosis table's confidence values are documented as provisional policy floors rather
  than measured estimates, at the point of definition. A deterministic lookup does not have
  confidence the way a model does; the numbers encode how much autonomy a mapping has earned.
  C8 replaces them with observed rates.
- Store access is serialised behind a re-entrant lock, and `open_case` holds it across the whole
  check-then-act. One sqlite3 connection was shared across FastAPI's threadpool with the
  thread-safety check disabled, so concurrent webhooks could commit each other's half-written
  rows — and even with the lock, two threads could both read "no such case" and race to insert
  the same payment. The unique index on `signals.payment_id` is the cross-process backstop.
- NPCI restricted execution windows corrected to both bands, `10:00-13:00` and `17:00-21:30` IST.
  The spec previously named only the first, which would have permitted an evening retry that NPCI
  forbids.
- Retry-after-alert interval corrected from 24 to 25 hours, matching Razorpay's documented
  initiation gap. The 24-hour figure is the RBI notification requirement; 25 hours is when the
  charge actually initiates.
