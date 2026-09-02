# Changelog

## [Unreleased]

### Changed
- The confidence thresholds in `Diagnosis.authority` now say at the point of definition that they
  are policy rather than measurement. `0.90` and `0.70` came from the specification and nobody
  measured them; C8 makes them answerable. See issue #7.

- C6 (reply understanding) joins C5 and the C8 holdout in the never-cut set. With 85% of documented
  failures resolving deterministically, reply parsing is where a model is irreplaceable, so cutting
  it would remove the answer to what the AI does. The diagnosis model seam is recorded against C6,
  where the plumbing is written anyway, and human-queue preparation against C7.

### Added
- The escalation ladder, entered at the rung the diagnosis warrants rather than always at the
  bottom. A broken mandate skips silent retries entirely, because no number of them fixes an
  expired card and the attempts would spend NPCI's allowance proving what is already known.
- An expected-value gate split by what it can honestly claim. A rung costing more than the whole
  amount at stake is refused with no assumption at all — a probability cannot exceed 1. Anything
  marginal rests on an assumed success rate, and any decision resting on one says so on the record.
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
