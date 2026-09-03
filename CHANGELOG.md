# Changelog

## [Unreleased]

### Fixed
- Approving or reclassifying a held case did not resume anything. `record_decision` only flipped
  the case's state; nothing scheduled the retry it had been blocking, so an approved case sat
  `OPEN` with no pending action until the customer happened to write in again. `resume_after_review`
  now re-arms it through the same NPCI-banded, cap-checked `schedule_retry` path every other retry
  uses — honouring "now" the way a realignment does, since the review itself was the wait, not a
  reason to add a further backoff on top of it. A case that had already exhausted its retry cycle
  before being held is refused here too, not silently re-armed.
- The scheduler terminal never showed what a reviewer decided. It filtered the audit trail down to
  `decision_type == "execution"` only, so `record_decision`'s "human-review" records — approved,
  rejected, reclassified, and by whom — were silently dropped; the log stopped at the cancellation
  that sent a case to a person and never said what the person then did about it. Found by watching
  it live: a case moved to review, a decision was made, and nothing in the scheduler's own terminal
  changed. The scanning logic is now `audit_lines`, its own function, tested directly rather than
  only through the poll loop it used to live inside.
- A promise realigned to a future day landed at whatever clock time the reply happened to arrive,
  not at the start of that day. "Tomorrow morning" replied to at 21:24 scheduled the retry for
  21:24 the next day — 24 hours out regardless of what "morning" meant. `validate_date` correctly
  reduces a promise to a day with no time of day of its own; realignment now combines it with the
  start of contact hours (08:00 IST) instead of `now`'s clock reading, matching how the codebase
  already answers "what time does a day begin" everywhere else. Caught live during a rehearsal.

### Added
- `test_two_different_payments_open_two_different_cases` — the real webhook endpoint, not the
  demo script, was where the bug below could have mattered. It doesn't: `grep`ing `src/unhalted`
  for anything resembling the demo's "read a fixed pool of files" pattern turns up nothing, and
  `/webhooks/razorpay` opens a case from whatever payload Razorpay actually posts, never from a
  local selection. This locks that in rather than leaving it argued.

### Fixed
- `scripts/session.py` no longer replays the same case forever. `real_signal` always read the
  alphabetically first captured fixture, so a database that already held a case for it matched
  straight back to that case on every later run — correctly, but it meant the three fixtures'
  three distinct real payments were never actually reached. It now walks the fixtures for the
  first `payment_id` the database has no case for, so running the script again gives the next
  real case rather than the same one, and only replays once every fixture has been used.
- `test_a_truncated_response_is_not_retried` passed only where a real API key happened to be on
  disk. `_call_model` returns before its mocked `httpx.post` is reached when
  `config.model_api_key()` is empty, which it always is in CI — the test now sets a fake key
  itself rather than depending on `.env`.
- The reviewer's decision no longer waits on the model. `show_case` called `brief()` — a live
  request, up to 60 seconds — before the approve/reject/reclassify prompt could even appear, and a
  silent multi-second wait with no indication anything was happening read as a hang rather than a
  wait. Found on a real run: 19 seconds of silence with the terminal apparently stuck. The raw
  material a reviewer decides from — signals, diagnosis, why it stopped — now prints and prompts
  immediately, with no model call at all. The model's read is a new `i`nsight option, fetched only
  if asked for, with a visible "thinking" line before the call so a wait reads as a wait.

### Added
- `scripts/schedule.py` — the scheduler's terminal. Every action as it is scheduled, comes due,
  executes, is deferred or is cancelled, as an append-only log rather than a redrawing table:
  the argument this view exists to make is about *sequence* — a charge was scheduled, then the
  customer said stop, then the charge did not happen — and a log lets a viewer read back up the
  screen and see the order for themselves. `--run` makes it the worker as well as the watcher,
  calling the same `run_due` the CLI and the HTTP endpoint call.
- `src/unhalted/tui.py` — terminal formatting in one place. Three views of one system had three
  copies of the same escape codes, which is how they end up looking like three systems. Everything
  degrades to plain text off a terminal, so piping a view into a file gives output rather than
  escape sequences.
- `Store.actions(state=...)` reads scheduled actions in any state. `pending_actions` cannot return
  a cancelled row, and a cancellation is exactly what the scheduler view needs to show.

### Changed
- The reviewer's terminal stays open. It exited the moment the queue was empty, which meant it was
  never running when a case arrived. It now polls, redraws, announces what appeared with a `NEW`
  marker, and closes only when the reviewer says so — using `select` rather than a thread, so the
  reviewer can sit and watch *and* act without the read blocking either.
- All three terminals carry a banner naming which view they are, and share one set of rules,
  chips, tables and relative times ("in 16h 05m" rather than a timestamp a viewer has to subtract).

### Fixed
- `--db` and `--at` work after a subcommand as well as before it. argparse puts an option on the
  parser it was declared against, so `unhalted --at X run-due` was correct and `unhalted run-due
  --at X` was an error — a distinction nobody should have to learn, and one I walked into a minute
  after adding the flag. Every subcommand now takes them too, with `SUPPRESS` as the default so an
  absent flag does not overwrite what the top-level parser already read.
- A database deleted while its write-ahead log survived now says so, and names the files to remove.
  In WAL mode SQLite keeps `-wal` and `-shm` beside the database, and `rm unhalted.db` leaves them;
  the next open failed with a bare `disk I/O error`. Resetting by hand is exactly what somebody does
  before a demo, so the message carries the remedy and the CLI prints it rather than a traceback.
- The audit trail no longer records "case-opened" for a case that was already open. Razorpay
  redelivers, and re-running the session script sends the same payment again — both correctly match
  the existing case, and recording an opening for either made the trail assert an event that never
  happened. `Store.open_case_or_get` reports novelty from inside the lock that decides it.

### Added
- `--at 'YYYY-MM-DD HH:MM'` on `unhalted run-due` and `scripts/session.py`, so the window rules can
  be rehearsed at any hour rather than only inside one. The library already took `now` everywhere —
  333 tests depend on it — and the scripts did not, which meant a run sheet could be tested at 3am
  and a rehearsal could not. Nothing about behaviour changes; only the instant the same rules are
  evaluated against.
- An override announces itself on stdout, where a recording would capture it. The risk was never the
  capability, it was using it silently and letting a stated time pass for a real one, so the safety
  is visibility rather than absence.

### Fixed
- A retry realigned by a promise-to-pay did not carry the payment method, so the same card case was
  unbanded when first scheduled and banded when realigned — moved for a UPI Autopay rule that does
  not reach cards, and recorded as a `WINDOW_VIOLATION` that never happened. The #30 fix threaded the
  method through one of the two `schedule_retry` callers and missed the other. Found while rehearsing
  against a forced clock.

### Fixed
- The action lease claimed and read in two statements, joined by `(worker, leased_until)` — a key
  that is not unique. A worker claiming twice inside one lease window re-read its earlier batch, and
  two workers with agreeing clocks collided. Four processes over 400 actions produced **386
  double-claims**, each of which would have been a debit attempted twice. Claim and read are now one
  `UPDATE ... RETURNING`, so there is no key to get wrong.
- `PRAGMA busy_timeout = 5000`. SQLite fails a locked write immediately by default; with more than
  one worker a claim will sometimes arrive while another holds the write lock, and returning
  `SQLITE_BUSY` to the caller would turn ordinary contention into a failed recovery action.
- `tests/test_store_concurrency.py` now runs real subprocesses for this. Threads share the test
  process's `Store` and its lock, so they cannot demonstrate what two deployed workers would do.

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

