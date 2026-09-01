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
