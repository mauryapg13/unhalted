# Changelog

## [Unreleased]

### Fixed
- The batch report's exposure rows read as a contradiction: Rs 12,514 labelled "unreachable by
  retry" while the agent's ceiling counted it. Both were right — the money is unreachable *by a
  retry*, and the agent routes it to re-authorisation instead — but the row now says which action
  it needs rather than leaving a reader to infer it. More importantly the ceiling is qualified in
  place: re-authorisation is diagnosed, priced and queued on this deployment and has no adapter to
  send. The first attempt at saying so put two lines of caveat under the headline figure and turned
  a result screen into a disclaimer; the gap line now names the action that closes it — "diagnosed,
  priced and queued to re-authorisation" — and the footer points at `unhalted capabilities`, which
  is the command whose whole job is stating what this deployment can and cannot do.

### Added
- `unhalted report` now leads with the money, sorted by what a retry can actually reach: unreachable,
  must-not-be-taken, unclassified, and contested — then both policies' ceilings at perfect
  conversion, the gap between them, and what closing that gap costs. The counted rows underneath say
  what each policy *did*; this says what was there to be done, which is the half a reader asks about
  first and the report could not answer. `unhalted breakeven` computed it already, but only over
  stored cases, so the 300-case batch had no version of it at all. Persisted to
  `docs/batch-measurement.json` by `scripts/run_batch.py` rather than recomputed, so the terminal
  view stays a read of one run's numbers.

### Fixed
- The batch report labelled a row "customer contacts" where the figure is contacts *scheduled*. The
  batch measures ladder decisions; the contact ceiling is enforced at send time in the runner, so
  under 1-per-14-days at most 100 of those 189 reach anybody inside a fortnight — the batch has 300
  cases across 100 distinct customers. Both readings of the old label were defensible and one of
  them contradicted a hard rule stated three sections earlier in the same README. No number moved.

### Fixed
- **A reply asking to cancel was swallowed by a weak opt-out read.** "Mujhe nhi chaiye. cancel kro"
  parsed as cancellation-request at 0.95 and opt-out at 0.50. `replies.decide` is ordered
  most-protective-first and each branch returns; opt-out is step 3 and clears its bar at 0.50 by
  design — missing a stop costs a compliance failure — so the weak signal fired and the strong one
  was never examined. Contact was suppressed, nobody was told to cancel anything, the case stayed
  `open`, and the mandate went on billing somebody the system had just promised never to contact
  again. The one thing they actually asked for was the one thing that did not happen. These are two
  different asks, not two readings of one: the stop still wins on contact, and a cancellation at or
  above its own threshold now also sets `needs_human`, the same shape `PRECEDENCE:DISPUTE_OVER_PROMISE`
  already had one branch above.
- `handle_reply` returned on `stop_code` before it could act on `needs_human`, so the outcome above
  would have been recorded and then ignored. A stop whose rule carries no terminal state — `OPT_OUT`
  is the one, because an opt-out does not settle the debt — now holds the case for a person when the
  reply needs one. Rules that name their own terminal state still win with it.

### Added
- Every nudge carries a payable link, `ASK_DATE` included. Asking a customer to name a date and
  giving them no way to just pay makes somebody who has the money today do extra work to hand it
  over — and rung 2 is named "message with a pay link", which was a contradiction on screen for the
  one variant that had none. The link is one Razorpay API call per nudge, which is the cost this
  originally avoided.

### Changed
- The terminal views read as one system rather than four. `unhalted case` had no banner at all —
  the one view an auditor lands on was the only one that did not say what it was. A long outcome
  was cut at a hard 96 characters, wider than the 92-column ceiling every other view respects, so
  it was cut twice and neither cut said so: `tui.ellipsis` shortens on a word boundary and marks
  it. A pending action printed a raw ISO timestamp with microseconds beside views that render
  every other time as `07 Sep 15:16  in 2d 00h`. `step=None` showed a Python word where a person
  reads an answer, now `—` via `tui.field`. Colour is used only where it already carries meaning:
  the confidence band is tinted by what it permits (`tui.authority` — green acts, amber acts and
  is sampled, red does not), the run-due counters dim their zeros so the ones that happened carry
  the line (`tui.counter`), and the confidence value is bold because it is what the gating rests
  on. No wording, no layout and no behaviour changed.

### Added
- `scripts/demo_inject.sh` — post N failed payments sharing one error shape over the real signed
  webhook, so a taxonomy gap can be demonstrated without a screenful of inline shell. Not a
  shortcut past the pipeline: each payment carries a real HMAC signature and goes to the same
  endpoint Razorpay posts to, so the signature check, the idempotency check, the normaliser and
  the taxonomy all run. It exists because `scripts/inject.py` covers the five *documented* card
  scenarios and showing an unclassified failure needs a reason that deliberately is not one of
  them. Refuses to start when its port is already in use, rather than posting into a stale server
  bound to a different database and leaving the cases apparently vanished — which is exactly what
  happened while working out the command it replaces.

### Added
- **A contact ceiling, one message per customer per fortnight** (`contact.max_per_window`, over
  `contact.window`), counted across
  every case they have and every channel. The retry cap bounds *debits* and says nothing about
  *messages*: a customer with four failing subscriptions consumed no retries at all — the balance
  flow asks before it retries — and still received four messages in four days, each individually
  correct, because nothing in the system had a view of the person. `windows.contact_budget` is the
  rule, `store.contacts_since` counts from the audit trail rather than a counter kept beside it,
  and `runner.execute_nudge` asks before every send. A message over budget is deferred to when the
  budget frees, never dropped: losing a customer's only notice that their subscription is lapsing
  is worse than delivering it late. Confirmations that a payment arrived do not count against it.

### Fixed
- The README listed that ceiling among the hard rules and `shell/notify.py`'s docstring said it sat
  above delivery, for some time before anything counted contacts. Both now describe what runs.
- The README's "Specification as test suite" section claimed every stopping rule in
  `tests/features/` "is a check that goes red if the shell weakens". The feature files are parsed
  for shape by `tests/test_specification.py`; no scenario is bound to executable steps, and
  `tests/step_defs/` holds only `__init__.py`. The constraints are tested — thoroughly, in
  hand-written pytest — but not through the Gherkin, and the contact ceiling is what that gap cost:
  specified in the feature file, advertised in the README, never once run. The section now says
  what actually executes.

### Fixed
- **A stop now outlives the queue it emptied.** `apply_stop` cancelled every pending action in
  scope and, for `OPT_OUT` — the one contact-barring rule with no terminal state, because an
  opt-out does not close the debt — that cancellation was the whole of its lasting effect. Nothing
  was written down, so the case stayed `open`, `handle_reply` had nothing to consult, and the
  customer's own next message re-armed the ladder: in rehearsal, a retry was scheduled two minutes
  after the audit trail recorded that contacting this customer would be a compliance failure. The
  rule's `suppresses_contact=True` had been read by nothing since it was written. A stop that bars
  contact now writes a `contact_suppressions` row at the rule's own scope, checked in three places
  — `handle_reply` refuses before the model is called, `handle_failure` opens a later case but
  schedules nothing against it, and the runner checks again before dispatch, because delivery is
  at-least-once. Nothing a customer says lifts one: not a date, not "actually, continue", not a
  payment.
- **The scheduler view stamped queue events with its own clock.** `SCHEDULED`, `CANCELLED` and
  `DUE` were dated to the poll tick that noticed the row rather than to when the thing happened, so
  opening the log against an existing database backfilled every historical event to the second the
  viewer started — the one thing an append-only log exists to keep straight. Cancellations had no
  time to read, so `pending_actions.cancelled_at` now records one and `cancel_pending` takes a
  `now` from all seven production call sites; `SCHEDULED` reads `created_at` and `DUE` reads
  `scheduled_for`. `store.py` still never invents a time — a caller that passes none leaves the
  column NULL and the viewer falls back to another time off the same row.
- The scheduler view printed an accurate log out of order. Events were gathered
  cancellations-first, then pending rows, then the audit trail — which reads correctly only for a
  scheduler that was already running when each arrived. Opened against an existing database the
  whole history lands in one batch and that grouping overrode chronology, printing a cancellation
  above the delivery a minute earlier that caused it. The batch is now ordered on the times its
  events carry, with a tie-break that keeps cause above effect within a single second: a reply is
  read, the stop it triggers fires, then the actions that stop cancels are cancelled.
- `scripts/session.py` froze `now` at startup and stamped the whole conversation with it, so
  replies typed minutes apart shared one timestamp and the audit trail could not order them. A
  reply is now stamped when it is typed; under `--at` the stated time still stands, since a
  rehearsal is meant to have a fixed clock.

### Added
- `unhalted stops` — who this system may not contact, and since when — and `unhalted lift-stop <id>
  --by <name>`, the only route back from a suppression. Deliberately a person's command with a name
  recorded beside the decision, and no code path from a customer reply reaches it.
- `tests/test_stop_is_durable.py`: six tests for the half of a stop that was never tested. Every
  stop rule was checked for firing correctly; none was checked for still being in force a message
  later, and all nine passed under that test.

### Changed
- The batch measurement is regenerated against the new balance flow, and the README's results table
  with it. Attempts fell from 217 to 84 and contacts rose from 56 to 189 in the same change: an
  empty account no longer gets three silent retries guessing at a payday, it gets one message asking
  when to try. The README now reads those two rows together rather than leaving them to be read as
  unrelated wins.

### Fixed
- The README's own "Honesty about the data" section claimed payment-status verification "runs
  against Razorpay test mode". `shell/verify.py`'s `RazorpayVerifier` is real and would work, but
  nothing constructs one, so the live path runs with no verifier at all. The behaviour is the safe
  one — a case that cannot be verified is held for a person rather than assumed unpaid — but the
  claim was wrong. It now sits under a new "Built, and deliberately not wired in" heading with the
  two other capabilities in that state: message drafting, and the upper ladder rungs.
- The thesis table said the model "drafts messages" without qualification. `core/draft.py` and its
  compliance lint are real and tested, and every message that actually goes out is one of the plain
  bodies in `shell/notify.py` — stated directly under the table, since that table is the project's
  central claim.
- `docs/capturing-fixtures.md` had the real `RAZORPAY_WEBHOOK_SECRET` written into it in plaintext.
  Replaced with a pointer to `.env`. **The value is still in git history and must be rotated before
  the repository is made public.**

### Removed
- `measure.baseline.agent_would_enter_at`, a two-line wrapper around `ENTRY.get` that nothing ever
  called.

### Added
- The README now carries the whole routing table — every documented `error_reason`, the
  `error_source` values that change the answer, the resulting diagnosis, confidence and entry rung,
  and the two rungs this deployment has no executor for. Generated from the code and verified end to
  end rather than described, because "most failures are unambiguous and resolve from a lookup table"
  was a claim a reader had no way to check. It also makes the two places `error_source` outranks the
  reason legible: a merchant-side fault, and `credit_failed` without a stated source falling below
  the confidence threshold instead of guessing which of two documented causes applied.

### Changed
- **An empty account is asked, not guessed at.** `RECOVERABLE_BALANCE` entered the ladder at
  `SILENT_RETRY` and spent NPCI's whole allowance on three blind attempts at a date nobody asked
  about — while `core/reply.py`'s own docstring argued the opposite: whether a retry works depends
  on when the customer will have money, and no API reports that. It now enters at `NUDGE` with a
  message that actually asks, arms a fallback retry behind it (`retries.reply_grace`, 25h, plus the
  usual backoff) for a customer who never answers, and a reply naming a date cancels that fallback
  and reschedules to the day they gave. Scoped to balance failures only: nobody can tell you when a
  gateway will recover, so `RECOVERABLE_TECHNICAL` still retries blind, correctly.

### Added
- `Store.increment_retry_count`, called once per executed retry — see Fixed below for why nothing
  counted attempts before.
- `agent.escalate_after_cap`, shared by every path that can be refused by the retry cap.
- Three nudge messages instead of one (`NudgeVariant`): the first-touch text, the balance question,
  and an "we tried, here's a link" message for a case whose retries are spent. Reusing the
  first-touch wording on somebody who just asked for a specific retry date reads as never having
  listened; the variant is recorded on the action, so the wording is the decision's to make rather
  than something the executor invents at send time. Asking for a date no longer generates a payment
  link at all, which also stops it spending Razorpay's test-mode link quota on a message that never
  carried one.
- `cases.nudges_suspended_until`, so a promise-to-pay actually suppresses the next nudge.

### Fixed
- **A merchant's own broken integration was retried like a bank decline.** Checked across every
  `error_source` Razorpay documents: seven of eight produced a silent retry on `payment_failed`,
  including `business` — their label for the merchant's own configuration being wrong. Their
  failure-analysis guide is explicit that "Business failures require corrective action rather than
  retries... simply retrying the same request won't resolve them." The taxonomy walks most-specific
  to least, so a source with no rule of its own inherited the reason's permissive wildcard; for
  these the source is the whole answer, so `MERCHANT_SOURCES` is now checked before the reason and
  the case is held for a person with no customer contacted about a problem they cannot fix. The two
  remaining documented business reasons (`international_transaction_not_allowed`,
  `invalid_currency`) got explicit rules: both already held safely via the unmatched fallback, but
  read as gaps in the table, so `propose_taxonomy_rule.py` would have kept filing them as rules
  somebody still had to write. Recorded in `BREAKAGE.md`.
- **The retry counter had two readers and no writer.** `case.retry_count` was set to 0 on insert and
  incremented nowhere, so `backoff_for` always returned tier one — the `2h`/`6h` and `1d`/`2d` tiers
  were real, tested and unreachable — and `retry_count >= RETRY_CAP` could never be true, leaving
  NPCI's three-attempt cap enforced only in unit tests that set the count by hand. Found by asking
  what the number would be after three attempts. Recorded in `BREAKAGE.md`.
- **A refused retry left the case with nothing pending and nobody told.** All three paths that ask
  for one — the first schedule, a promise-to-pay realignment, a reviewer clearing a held case — wrote
  "refused" to the audit trail and stopped. They now fall through to `escalate_after_cap`, which
  walks `ladder.next_rung` to the next rung this deployment can actually execute (re-authorisation,
  voice and human callback have no executor, and it says so in the record rather than scheduling a
  dead action) and sends a payable link with the reason it is arriving. A customer who names a date
  the cap can no longer honour gets that link rather than silence.
- **`ReplyOutcome.suspend_nudges_until` was computed and read by nothing.** A customer who said "the
  8th" could still be nudged on the 7th. It is now persisted on the case and enforced in
  `execute_nudge`, which defers to the named date the same way it defers outside contact hours.
- **The amount ceiling ran on one retry path out of four.** A debit above the ₹15,000 card ceiling or
  the mandate's own `max_amount` was refused when first scheduled, and then permitted if a reviewer
  re-armed it or a promise-to-pay realigned it — the amount did not change because somebody asked
  again. One `_amount_permitted` check now runs on all four.
- `measure/compare.py` did not count a balance case's fallback retry as a scheduled attempt, which
  would have undercounted this system's own debit attempts against the baseline's — the direction of
  error this project can least afford, given that comparison is its headline number.
- `core/scenarios.py` supplied `error_source = "gateway"` for all five of its injectable scenarios
  alike — a placeholder that matched the three real captured fixtures (issue #8) but was never
  grounded in what each of these five specific reasons actually is. `core/taxonomy.py`'s own rule
  for `payment_timed_out` reads a different class for `"customer"` than for `"bank"`, and
  `"gateway"` was neither, so it fell through to the ambiguous fallback every time instead of
  reaching `NOTIFICATION_GAP`. `ERROR_SOURCE` is now a mapping, one entry per reason, each quoting
  the source `core/taxonomy.py`'s own rule already names — `payment_timed_out` now correctly reaches
  `NOTIFICATION_GAP` at `DIRECT`/1.0 confidence. Verified live via `inject.py` and `classify.py`.
  Recorded in `BREAKAGE.md`.
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

