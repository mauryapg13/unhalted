# Changelog

## [Unreleased]

### Fixed
- `scripts/session.py --scenario` run a second time against the same database matches back to the
  same case (by design — deterministic `payment_id`) and correctly prints no message box, since
  nothing was newly due. Step 4 still said "that boxed text above is what just arrived" anyway,
  pointing at nothing — it checked whether the case was *ever* delivered a message, reusing that
  for a claim about *this run* specifically. Now split: a new `execution.at == now` check requires
  the delivery to be from this exact pass before saying so; a repeat run says plainly that the case
  was already contacted earlier instead. Recorded in `BREAKAGE.md`.
- Every hard stop, and a reply's `needs_human` path, writes a real `stop` audit record — but
  `scripts/schedule.py`'s live view only ever surfaced one from a *cancelled pending-action row*,
  which a stop landing on a case with nothing left queued (a nudge that had already delivered, say)
  never produces. Found live: a cancellation reply correctly held a case for a human, and the
  already-running scheduler showed nothing at all for it. `audit_lines()` now also renders `stop`
  (as `STOPPED`) and `reply` (as `REPLY`, printed before whatever it caused). Checked against every
  `decision_type` actually written anywhere in the codebase to confirm these were the only two
  silently dropped. Recorded in `BREAKAGE.md`.
- `scripts/schedule.py`'s live view stamped a backfilled execution with the poll tick that noticed
  it, not `record.at` — the time it actually happened. A rehearsed `--at 08:00` execution, watched
  by an already-running scheduler polling real time, displayed at whatever second the viewer
  happened to poll. Outside contact hours, that reads as the exact violation the contact-hours rule
  exists to prevent, on a run that never did that. Recorded in `BREAKAGE.md`.

### Added
- `scripts/session.py --scenario REASON` — a real captured payment never reaches a contact rung
  (issue #8), so the reply loop was previously unreachable by any command in the repository.
  `--scenario` builds the same injected signal `scripts/inject.py` does, so `--scenario
  authentication_failed` opens a genuine `NOTIFICATION_GAP` case with a real pay link and the
  conversation actually attached to it — verified live, including a Hinglish promise-to-pay
  realigning the retry (`PROMISE_ACCEPTED`, `retry realigned to 2026-10-02`).
- The reply loop now notices a payment landing while it waits, instead of only ever reacting to
  what you type. It used to block on `for line in sys.stdin`, which cannot see anything that isn't
  a line of input — a customer who pays the link mid-conversation got no acknowledgement until
  someone typed another reply, if ever. `select()` now polls the case's own state once a second
  alongside waiting on stdin, so `payment received — this case is settled` prints and the script
  exits the moment `mark_recovered` closes the case, verified live with nothing ever typed at all.

### Fixed
- `scripts/session.py`'s reply prompt gave no indication of what you were replying to — the actual
  message printed two screens up, in step 3, under a generic "whatever the ladder scheduled
  actually runs" title that reads as an internal log line, not a text message arriving on a phone.
  Step 3 now titles itself "The message reaches the customer" when the diagnosis reaches a contact
  rung, and step 4 opens by pointing back at the boxed text above it before asking for a reply.

  The first version of this fix checked only whether the diagnosis's rung *can* contact someone,
  not whether it actually did *this pass* — a nudge deferred by contact hours (08:00-19:00 IST,
  the same window this system enforces everywhere else) sends nothing at all, and step 4 still
  claimed a message had "just arrived." Caught live, outside the window: now checked against the
  audit trail's own execution record, and a deferred nudge gets its own honest step 4 explaining
  nothing has reached the customer yet, with the `--at` rerun that would actually deliver it.
- `scripts/session.py` asked for a reply as the customer even on a silent retry — a diagnosis that
  by design never contacts anyone, so there was never a message to be replying to. It now checks
  whether the entry rung actually contacts the customer before offering the prompt, and explains
  why there's nothing to reply to when it doesn't. Recorded in `BREAKAGE.md`.

### Added
- The README documented the ladder, the batch, and the reply corpus, but not the two things that
  actually close a case or change its own rules: the payment-recovery loop (`payment_link.paid` →
  `RECOVERED`, cancelled actions, a real confirmation) and the two proposers
  (`propose_policy_change.py`, `propose_taxonomy_rule.py`) that change `config/policy.yaml` and
  `core/taxonomy.py` from free text without ever writing either file themselves. Added two new
  sections for both, `run-due` and the operational scripts (`inject.py`, `session.py`,
  `schedule.py`, `review.py`) to the command list, and brought Layout up to date with what the
  tree has actually held for a while — `agent.py`, `runner.py`, `store.py`, `cli.py`, and
  `scripts/` were all missing from it entirely.
- A customer who pays through the recovery link is told so. `mark_recovered` closed the case and
  cancelled its pending actions but never sent a word back — the same silence a real WhatsApp
  thread never would. It now sends a plain confirmation through the same notifier and the same
  contact-hours gate a nudge uses, so nothing about a paid case is quieter than one still being
  chased.
- `unhalted calibration` — whether confidence predicts outcome, measured on real terminal cases
  only, never generated ones. Issue #7 could not be settled on generated data because the correct
  class there is whatever the generator picked; `mark_recovered` gives some cases a real,
  non-generated outcome for the first time, and this groups them by the confidence band their
  diagnosis fell into. A band under 20 cases is shown, not hidden, but explicitly flagged as too
  few to conclude anything from — the honest state of things right now, not a bug in the report.
  Revoked and false-failure closures are excluded from the recovery rate entirely rather than
  counted as failures, since recovery was never the live question for either.
- `scripts/propose_taxonomy_rule.py` and `core/taxonomy_proposal.py` — every `UNKNOWN`-diagnosed
  held case was previously a dead end: nothing fed it back toward closing the taxonomy gap that
  caused it. This clusters held cases by their exact `(method, error_reason, error_source,
  error_step)` — plain grouping, no model needed — and, for a chosen cluster, proposes a rule
  grounded in Razorpay documentation supplied by the caller, using the same evidence-quote
  discipline `policy_change.py` already established. Never writes to `core/taxonomy.py`. Found a
  real instance of the exact failure mode the prompt explicitly warns against while testing this
  live: the model marked a rule `DIRECT` while its own rationale admitted the cause "is not
  communicated" — recorded in `BREAKAGE.md`, and the reason nothing here applies a proposal
  automatically.
- `tui.spin` — a shimmering label that runs while a blocking model call is in flight, on a
  background thread, and clears the moment it returns. Wired into the reviewer's briefing and
  `propose_policy_change.py`, the two places a script waits on a live call with the terminal
  otherwise silent. Off a terminal it prints the label once as a plain static line rather than
  animating, matching every other degrade-off-a-tty rule in `tui.py`.
- The loop `shell/paylink.py` opens now closes. A payment link is tagged with `reference_id=case.id`
  (a documented field, verified against `api/payments/payment-links/create-standard.md`) when
  created; `ingest/webhooks.py` now handles `payment_link.paid` (verified against
  `webhooks/payment-links.md`), reads that reference straight back off the payload, and calls the
  new `agent.mark_recovered` — the case moves to `CaseState.RECOVERED` (defined since the model was
  written, never previously set anywhere), every pending retry and nudge is cancelled, and the real
  payment id and amount are recorded in the audit trail. A paid link for an unknown case, or one
  with no reference_id at all, is acknowledged and ignored rather than raised. This is what makes
  "the customer paid a different way" a real, observable event instead of a retry quietly running
  forever against money that already arrived.
- `scripts/schedule.py` distinguishes an escalation from a routine cancellation. `HELD_FOR_HUMAN`,
  `DISPUTE`, `DISTRESS`, `CHARGEBACK` and `REG_HOLD` — every stop whose terminal state is
  `CaseState.HELD_FOR_HUMAN` — now render as `ESCALATED`, not `CANCELLED`; a promise-to-pay
  realignment or a routine stop still reads as `CANCELLED`, since it is one. A recovery is no
  longer double-reported — it already gets its own `RECOVERED` event from the audit trail, and the
  matching `cancel_pending("RECOVERED", ...)` entry is now suppressed rather than printed a second
  time as an unlabelled cancellation.
- `unhalted policy` — prints the currently loaded policy (which file, its version, every NPCI
  band, contact hour, retry cap, backoff tier, confidence threshold, reply-policy threshold,
  ladder cost, mandate limit), read from the live `unhalted.policy.POLICY` object rather than the
  raw YAML — so it reflects a real `UNHALTED_POLICY` override, not just the shipped file. Closes
  the gap where the only way to check "what is configured right now" was asking someone to read
  `config/policy.yaml` for you.
- `config/policy.yaml` and `unhalted.policy` — a single, validated source for every numeric
  threshold this system enforces: NPCI bands, contact hours, the retry cap, backoff tiers,
  confidence thresholds, reply-policy thresholds, ladder costs, mandate limits. Every module that
  used to hardcode one of these (`windows.py`, `scheduler.py`, `models.py`, `replies.py`,
  `ladder.py`, `limits.py`) now reads it from here, mapping the plain string/number keys onto its
  own domain types itself — `unhalted.policy` never had to learn what a `DiagnosisClass` or a
  `Rung` is. Migrated one module at a time, full suite run after each, so every value is confirmed
  identical to what was previously hardcoded rather than assumed to be. A change to the file is
  live the next process restart, confirmed directly: setting `retries.cap` to 1 in a copy of the
  file and pointing `UNHALTED_POLICY` at it drops `scheduler.RETRY_CAP` to 1, no code change.
  Exists because the same NPCI-band concept was already duplicated, and wrongly diverged, across
  three separate code paths in this project (see `BREAKAGE.md`) — one file, loaded once, is the
  fix for that entire bug class, not only the specific instances of it found so far.
- `scripts/propose_policy_change.py` and `core/policy_change.py` — reads free text describing a
  regulatory change and proposes a field-level diff against `config/policy.yaml`. Same distance
  between recommending and acting as everywhere else the model touches something that matters: it
  never writes to the file, a proposed field outside a fixed closed set is refused before being
  shown, and a proposed value's quote is checked against the actual input text (reusing the same
  evidence check reply parsing already used, now shared via `core/evidence.py`). Backoff tiers,
  confidence thresholds and reply-policy thresholds are deliberately not proposable — those are
  this project's own risk tolerance, not something a circular states. Smoke-tested live against a
  real NPCI-circular-shaped text: correctly proposed an exact band change, correctly declined to
  guess at a vaguely-worded mention, correctly treated "unchanged" as nothing to propose.

### Changed
- Every live model call now sets `reasoning: {"effort": "low"}`. `z-ai/glm-5.3-flash` reasons
  before it answers, billed to the same completion budget as the visible output — measured
  directly, a trivial prompt at default effort spent its entire budget on invisible reasoning and
  returned no content at all, and a realistic policy-change call spent 85-95% of its tokens the
  same way, with reasoning length varying enough call to call (887 to 1608 tokens on an otherwise
  identical request) to be the actual source of the multi-second-to-30-second latency swings
  reported live. Lowering it cut reasoning tokens to single digits and latency 3-9x on the hardest
  cases in `tests/fixtures/replies/labelled.json`, with no accuracy loss measured against them —
  the full 68-reply evaluation re-run under it: 68/68 parsed (was 67/68), `opt-out` and `distress`
  recall still 1.00, zero forbidden intents, cost per parse roughly halved. `docs/reply-evaluation.md`
  and the README's results table both carry the re-measured numbers rather than the pre-change ones.
- `scripts/evaluate_replies.py` now measures and reports latency and cost per parse directly,
  rather than the report only covering accuracy.
- The batch report's modelled money table (Part two) now leads, ahead of the counted facts (Part
  one) that justify it. It's the number everyone asks for first; the reordering says so in its own
  first line, and the counted section it depends on follows immediately after rather than
  fifteen screens later.

### Fixed
- `unhalted report` printed `docs/batch-measurement.md` straight to the terminal — every `|`,
  every `**bold**`, every `##` heading, literally. `scripts/run_batch.py` now saves the numbers
  behind the doc to `docs/batch-measurement.json` alongside it, and `measure/report.py` gained
  `render_terminal()`, a plain-text sibling to the existing markdown `render()` sharing the same
  `modelled_recovery()` math — refactored to take the batch's total exposure directly rather than
  the case list it was summed from, which is also what let the terminal render reuse it without
  needing the full generated batch back in memory. The full doc is still one line away for whoever
  wants the argument behind a number, not just the number.
- `scripts/session.py` scheduled `"nudge"` by hand and delivered it itself, regardless of what the
  diagnosis actually was — a script exercising a shortcut and calling it the pipeline. It now calls
  the real `run_due()`, the same function the CLI and the HTTP scheduler call, so whatever the
  ladder actually scheduled — a retry, a nudge, or nothing — is what runs, contact hours included.
- The ladder's escalation had never actually run through `run_due`. `agent.py` scheduled every
  non-`SILENT_RETRY` rung with `kind=Intervention.name` (prose for a human, e.g. "message with a
  pay link") instead of a key `runner.EXECUTORS` holds, and `scheduled_for=None`, which the
  claiming query's `scheduled_for <= now` can never satisfy — either fault alone left the row
  permanently unclaimable. Found live: injecting `authentication_failed` scheduled a nudge that
  `unhalted case` showed correctly but `unhalted run-due` reported `claimed=0` for. `ladder.py`'s
  `_SLUG` map is now public `SLUG` and `agent.py` schedules with `ladder.SLUG[rung]` and a real due
  time, matching how a retry is scheduled. `NUDGE` now genuinely executes end to end;
  `REAUTHORISATION`/`VOICE_CALL`/`HUMAN_CALLBACK` still have no registered executor and now say so
  honestly rather than *also* being unschedulable. A prior test had asserted the broken `kind`
  string as correct; fixed, and a new integration test proves the row is claimable and executed,
  not just shaped right. Recorded in `BREAKAGE.md`.
- `scripts/propose_policy_change.py` silently blocked forever when run with no `--file` and
  nothing piped in — waiting on `stdin`, correctly, but with nothing printed first, which is
  indistinguishable from a hang. Caught live: it sat for 30 minutes before being reported. The
  same shape as the reviewer-hang fix from earlier this session, on a script built the same week
  and missing the lesson: an interactive terminal now sees what it's waiting for and how to
  finish (`Ctrl-D`) before the read blocks; piped input and `--file` are unaffected, confirmed by
  tests that fake both paths rather than assumed unaffected.
- The README claimed Razorpay's error-scenario test cards "yield specific documented error
  reasons rather than generic failures." That was already settled negatively — issue #8, tested
  on both checkout surfaces available to this account — and the README had simply never been
  updated after the finding. Corrected to match `CHECKPOINTS.md`'s own recorded fact.
- A redelivered payment failure was scheduling a second retry. `handle_failure` gated diagnosing
  and scheduling on whether *this call* had just created the case row, which is a different
  question from whether the signal had actually been worked — `ingest/webhooks.py` creates that
  row itself, for durability, before it ever calls `handle_failure`, so the row already existed on
  every delivery including a payment's genuine first one. One payment under two event ids produced
  two scheduled debits for a failure that happened once; the same first delivery was also
  mislabelled "signal already known" in the audit trail, the opposite of what an earlier pass fixed
  that wording to prevent. Both now key off whether a diagnosis has actually been recorded for the
  case, which is true regardless of which caller created its row first. Found building
  `scripts/inject.py`, reproduced directly against the real webhook endpoint.

### Added
- `scripts/inject.py` and `scripts/classify.py` share `core/scenarios.py` — the same five
  documented card reasons, once, rather than two lists that could quietly disagree.
- `scripts/inject.py` — runs one of those five scenarios through the real pipeline: a real case,
  a real recorded diagnosis, a real scheduled action, visible in the reviewer and scheduler
  terminals exactly as any other case would be. Explicitly not a webhook, and says so on every run
  — `scripts/classify.py` shows what the rule table *would* say without a case to show it on;
  this is the other half, for showing several different real, audited cases without waiting on
  `docs/capturing-fixtures.md`'s real capture procedure for each one. Run it with no argument, or
  `--list`, for a numbered menu rather than needing the exact reason string typed out.
- `scripts/classify.py` — what the taxonomy says for five of Razorpay's own documented card
  error scenarios (`docs/capturing-fixtures.md`'s test-card table), called directly against
  `diagnose()`. Not a payment and not a case: the three fixtures actually captured on this account
  all carry the same reason, which is why every rehearsal has landed on the same class so far. Two
  of the five reach full confidence and auto-execute; the captured ones sit at 0.80.
- A nudge now carries a real, payable Razorpay Payment Link instead of asking the customer to
  reply. The escalation ladder already priced this — rung 2 is "message with a pay link", rung 3 is
  "re-authorisation link" — but no code generated one; a customer whose card had expired had nothing
  to do but wait on a retry that could not succeed. `shell/paylink.py` calls Razorpay's documented
  `POST /v1/payment_links` (verified against `api/payments/payment-links/create-standard.md`, not
  assumed) with `notify` both off, since we deliver the message ourselves. A link that fails to
  generate — no key configured, Razorpay refuses, the network is down — does not hold the nudge; it
  goes out without one, logged, not raised. The network call itself is deferred until after the
  contact-hours check, so a nudge already known to be deferred does not spend one for nothing.
  `nudge_body` is now shared between the customer terminal and the runner, replacing two
  independently hand-rolled message strings that had already drifted apart.

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

