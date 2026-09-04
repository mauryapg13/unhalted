# Checkpoints

The purpose of this file is to stop the build drifting. It is the only place that decides what
counts as done. If something is not written here, it is not in scope.

---

## The goal, stated once

Build an agent that takes failed mandate debits on Razorpay, works out why each one failed, runs a
bounded recovery sequence, and reports honestly how much money it actually recovered — with the
model doing the reading and deterministic code doing the deciding.

Deadline: **applications close 5 September**. The submission is a public repo, a five-minute pitch
video, and a written answer about what broke.

Nothing about that goal changes. Everything else is negotiable.

---

## How to use this file

**For me.** Work top to bottom. Do not start a checkpoint until the one above it passes. When
tempted to build something, find it in this file first. If it is not here, it does not get built.

**For an AI agent.** These rules are binding:

1. Work on the lowest unmet checkpoint. Do not skip ahead.
2. Do not add functionality that is not listed in the checkpoint you are working on.
3. If you believe something is missing from this file, say so and wait. Do not build it and ask
   afterwards.
4. A checkpoint is done when its "Done when" line is literally true. Not nearly true.
5. If a checkpoint cannot be met, say so plainly and move to the degradation ladder at the bottom.
   Do not quietly substitute something easier and call it done.

---

## The divergence test

Before building anything, ask: **does a case need this to flow correctly through the pipeline?**

If yes, build it properly. If no, do not build it at all — not "quickly", not "simply". This is what
rules out a second merchant, authentication, deployment, and every refactor that does not unblock a
checkpoint.

## Nothing exists only for the demo

The video is a recording of the working system. It is not a performance of one.

This is a hard rule, and it is the one most likely to be broken under time pressure:

- No `DEMO_MODE` branch that changes behaviour
- No hardcoded or canned responses anywhere in the code path
- No screen reading fixture files while presenting itself as live
- No path that works only with the exact inputs used while filming

**The one thing that is allowed, because it is honest:** replaying real Razorpay payloads captured
from test mode to reach batch volume. That is real data at synthetic volume, it is labelled as such
in the README, and it goes through the identical code path as a live webhook. Replaying real data is
not the same as faking output.

The test: if a judge clones the repo, runs it against their own test keys, and pokes at it for ten
minutes, everything they touch should work. Anything that would not survive that does not ship.

## The repo bar

True of every module, whether or not it is ever filmed:

- Real failure paths are handled — a bad signature, a timeout, a malformed payload, a model
  returning nonsense. Not caught-and-ignored; handled and logged.
- Tests exist for the logic, not only for the scenario that appears on screen.
- No dead code, no commented-out experiments, no TODO standing in for something the demo skipped.
- Anything unfinished is absent, not stubbed. A missing feature is honest; a hollow one is not.

---

## Checkpoints

### C0 — The outside world is verified
Credentials exist and every external service answers.

**Done when:** `python3 scripts/preflight.py` exits 0, and the Plans and Subscriptions rows are
recorded in this file under "Known facts" below, whatever they say.

**Serves:** nothing in the video. It exists so that no later checkpoint fails for a reason we could
have known on day one.

---

### C1 — The repository runs
Specification executes as tests, in CI, on a clean machine.

**Done when:** `uv run pytest` passes locally and the CI badge is green on `main`.

**Serves:** the "build quality" judging criterion, and the repo a judge will open.

**Not included:** every scenario passing. Scenarios not yet implemented are skipped, not deleted.

---

### C2 — One case flows end to end
The thinnest possible complete path. Ugly is fine. Narrow is fine. Broken is not.

**Done when:** a `payment.failed` payload posted to the local webhook endpoint opens a case,
produces a diagnosis, schedules a retry in a permitted window, writes an audit record, and the whole
timeline is retrievable by case id.

**Serves:** everything. Nothing after this point is possible without it.

**Not included:** more than one error reason. Real Razorpay data. Any customer contact.

---

### C3 — Diagnosis runs on the real taxonomy
Failures are classified from Razorpay's actual error fields, not invented codes.

**Done when:** the rules table maps `(method, error_reason, error_source, error_step)` to a class
for every documented Razorpay card and UPI error reason, unknown strings are forced to `unknown`
and held, and each diagnosis records whether it came from the table or the model.

**Method is in the key** because ambiguity is method-specific: Razorpay documents one root cause for
`payment_timed_out` on cards and two on UPI, so the same reason is determined in one and undetermined
in the other.

**Serves:** the 1:00 beat — "most failures never reach the model".

**Not included:** issuer-specific historical priors. Pattern alerting across cases.

---

### C4 — The data is real
The pipeline runs on genuine Razorpay test-mode output, not fixtures we wrote.

**Done when:** a real failed payment created in test mode with an error-scenario card arrives over a
real webhook with a verified signature, opens a case, and is diagnosed correctly — and the captured
payloads are saved to disk for later replay.

**Serves:** the credibility of every number that follows.

**Not included:** volume. Real UPI insufficient-funds declines, which test mode cannot produce.

---

### C5 — The shell refuses the model
Hard rules are enforced and the refusals are visible.

**Done when:** a model recommendation of 18:30 is rejected against the 17:00–21:30 NPCI band and
logged as `WINDOW_VIOLATION`; the retry cap of 3 cannot be exceeded at any confidence; the ₹15,000
ceiling and the mandate's own `max_amount` are checked before every debit; and all **nine** stop rules
fire and cancel pending actions atomically.

**Serves:** the 1:45 beat, which is the strongest moment in the video.

**Also required:** retry backoff. A technical failure currently schedules its retry for the same
instant it failed, which retries into the same outage and burns one of the three attempts NPCI
allows. Backoff is a timing policy and belongs in the scheduler beside the window rules, applied
before the window check so the result is still moved out of a restricted band. See issue #4.

**Not included:** an admin surface for triggering merchant pause by hand. The rule lands; the
button does not.

---

### C6 — Hinglish replies are understood
Free text changes what the agent does next.

**Done when:** a promise-to-pay in Hinglish realigns the retry and suspends nudges; a reply
containing both a promise and a dispute halts everything on the dispute; opt-out suppresses contact;
an invalid date is rejected by the shell; and precision and recall per intent are measured on a
written fixture set and recorded, including the failures.

**Serves:** the 2:25 beat, and the only honest answer to "why do you need a model at all".

**Also built here:** the seam for a model call in diagnosis. C6 is when the model first enters the
codebase, so the plumbing is written anyway. `diagnose()` gains a path to say "I cannot decide yet —
verify the payment status and ask me again", which is the reconciliation loop Feature 1 describes and
which no rules table can produce. One implementation today; room for the second.

**Decided by C4's data, not now:** whether to wire the model for ambiguous diagnosis. Real payments
will show how many cases land below the 0.70 threshold. Two in five hundred means do not build it;
eighty means build it.

**Not included:** conditional promises. The clarification loop. Regional languages beyond Hinglish.

---

### C7 — Escalation is gated
Interventions are chosen and are stoppable on cost.

**Done when:** the ladder selects an entry rung from the diagnosis; the expected-value gate
terminates a low-value case as uneconomic with the calculation logged; and a drafted message
containing an invented discount is blocked by compliance lint and regenerated.

**Serves:** the 2:55 beat, and the answer to "isn't this more expensive than the money you recover".

**Also built here:** human-queue preparation. Every held case costs a person about a minute of
reading and sixty rupees of their time. The model summarises the signals and states a hypothesis with
what it weighed, so the decision takes thirty seconds. The specification already asks for this —
"queued for human review with the agent's best-guess hypothesis attached".

**Not included:** real voice calls. Real human callbacks. Both are simulated.

---

### C8 — The number exists and is honest
What can be counted is counted. What cannot is modelled and labelled as such.

**Done when:** a batch of at least 300 cases runs to completion against a 10% holdout receiving
Razorpay's documented blind-retry behaviour, and the report is split into two parts that a reader
cannot confuse.

**Part one — countable, no assumptions.** These are facts about what the two policies *do*, and
none of them needs to know whether anything recovered:

- futile attempts avoided — a blind retry spends all three NPCI attempts on a `card_expired` case;
  the agent spends none
- NPCI window violations avoided — the baseline has no window logic
- messages not sent — nudging somebody about their own bank's downtime
- contact-ceiling breaches avoided
- cases held for a human, and how many fell below each confidence band
- inference spend, measured — OpenRouter reports cost per call
- intervention spend, from the ladder's own costs

**Part two — modelled, and labelled on the page.** Rupees recovered, as a sensitivity range across
success rates rather than a point estimate, with the rates printed beside it.

**Why it is split:** on a generated batch, whoever writes the outcome model decides how much was
recovered. Reporting that as a measurement would be the worst honesty failure in the project. See
issue #10.

**On issue #7:** C8 cannot settle whether `0.90` and `0.70` are the right thresholds — that needs
ground truth for classification correctness, and on generated data the correct class is whatever
was generated. What it *can* do, and must, is measure **how much the thresholds matter**: the
distribution of cases across the confidence bands. If 2% fall below 0.70 the cut-point is nearly
irrelevant; if 40% do it is the most consequential number in the system. That is countable.

**Serves:** the 3:05 beat, and the track's stated bar — which on a test account translates to
"measured waste eliminated, plus a modelled recovery range", stated rather than implied.

**Not included:** statistical significance testing. Multi-month simulation. Any claim that the
recovery figure is a measurement.

---

### C9 — The system is observable
A case's full state and history can be read from the terminal, without opening the database.

**Done when:** `unhalted case <id>` prints a full timeline end to end — signals in, diagnosis,
rejections, messages, replies, outcome — from a real query against real stored state; `unhalted
report` prints the batch numbers from the measurement tables; and the service logs decisions as
structured lines as they happen.

**Serves:** operating the system, debugging it, and filming it — in that order.

**Not included:** a web dashboard. A live-updating view. Any server-rendered UI. The interfaces are
structured logs, JSON endpoints and the CLI.

**Amended.** "A live-updating view" originally sat in the excluded list, written against a web
dashboard. Three terminal views that poll the store are not that: a reviewer's queue that exits when
it is empty is a queue nobody is watching when a case arrives, and a scheduler with no view of what
it holds cannot be operated at all. They read real rows, invent no events, and print plain text off
a terminal. The exclusion still stands for anything served over HTTP.

**Optional, last, only if C10 is otherwise met:** `unhalted report --html` writing a static file
from the same measurement tables. A generated report, not a dashboard — no server, no live wiring.

---

### C9b — One case, both policies, side by side
What the agent did, against what Razorpay's documented behaviour would have done with the same
failure. On one real case, from stored state.

**Done when:** `unhalted compare <id>` prints the two policies as parallel timelines over the same
signal, and a counted summary beneath them — debit attempts, attempts a retry provably cannot fix,
attempts inside NPCI restricted bands, customer contacts — with every agent-side number read from
the audit trail and every baseline-side number produced by `measure/baseline.py`.

**Why it exists.** The strongest facts in this project are all comparative, and until now every one
of them lived in a batch report read after the interesting part was over. A single case shows no
contrast at all: the agent refuses a futile retry, and a reader with nothing to compare it against
sees a system doing nothing. This is also the view a merchant needs to decide whether to keep the
agent running, which is why it is a capability rather than staging.

**Serves:** operating the system, and the video's central beat — in that order.

**Not included:** any claim about what either policy *recovered*. The comparison stops at what each
policy does, for the same reason the batch report splits in two. A `compare` that reported rupees
would be the batch report's dishonesty at case scale.

**Ordering.** Worked before C10, because it changes what the video shows and the video is C10.

---

### C9c — The money question is answered without inventing an answer
What the money argument rests on, stated so that nothing in it is a forecast.

**Done when:** `unhalted breakeven` prints, from real stored cases or a batch: money sorted by what
each policy can reach; the recovery rate at which the intervention repays its own cost; and the
ceiling on what each policy could recover even at perfect conversion. Every figure is arithmetic on
a measured cost and a documented failure class. No line in it is a recovery estimate.

**Why it exists.** "How much money?" is the first question anyone asks, and this project cannot
answer it — recovery needs real outcomes at volume, and whoever writes an outcome model decides the
result. Breakeven inverts the unknown: instead of guessing the conversion rate and reporting the
rupees, it reports the conversion rate at which the rupees stop being worth chasing. That is
answerable, checkable, and a merchant can compare it against their own history in seconds.

**Serves:** the question a judge asks first, and the one a merchant asks before deploying.

**Not included:** rupees recovered as a point estimate, under any circumstances. A published
benchmark borrowed from another market as a substitute for measurement — card-updater recovery in
the US is a different mechanism from an Indian re-authorisation needing an MPIN, and citing one
would be the same invention with a footnote attached.

---

### C9d — A scheduled action actually happens
The seam between deciding to recover money and recovering it.

**Done when:** `unhalted run-due` and `POST /internal/run-due` execute the actions whose time has
come, claiming them under a lease so two workers cannot take the same one, reclaiming a lease whose
worker died, refusing an action a stop rule cancelled while it was held, and recording every
execution in the audit trail beside the decision that caused it.

**Why it exists.** `pending_actions` had three writers and no runner. A retry scheduled for 13:00
was a row describing an intention, and 13:00 arrived and nothing happened. Every part was correct
and the seam between them did not exist — the same shape as the three bugs in `BREAKAGE.md`, and
the most consequential instance of it.

**The queue is the table.** No broker. A row with a due time and a state is durable already, and it
shares a transaction boundary with the audit trail and the stop rules, so a revocation and the
retry it cancels cannot interleave wrongly. Delivery is at-least-once, because a lease that expires
returns its work; the execution side must stay idempotent for that to be safe.

**Not included:** a debit adapter. Initiating a charge needs a live mandate token and this account
cannot register one for UPI at all, so `retry` refuses, records why, and routes to a person.
Absent and saying so, not stubbed and lying. Also absent: authentication on the endpoint, and any
claim that this system has recovered money in production.

---

### C9e — Policy lives in one file, and a change to it is proposable
NPCI and RBI requirements change; the numbers this system enforces should not be an archaeology
project across six modules when they do.

**Done when:** every threshold this system enforces — NPCI bands, contact hours, the retry cap,
backoff tiers, confidence thresholds, reply-policy thresholds, ladder costs, mandate limits — is
read from `config/policy.yaml` through `unhalted.policy`, not hardcoded in the module that uses
it; `scripts/propose_policy_change.py` reads free text describing a change and proposes a
field-level diff, checked against the exact words that justify each proposed value.

**Why it exists.** The same NPCI-band concept was independently duplicated, and wrongly diverged,
across three separate code paths in this project before being caught (`BREAKAGE.md`). A concept
that exists in one place cannot diverge from itself. Separately: a merchant running this against
real, changing regulation needs a way to update it that is faster than a code review, and safer
than a human retyping a number by hand from a PDF.

**The model proposes, exactly as far as anywhere else in this project.** It never writes to
`config/policy.yaml` — `propose_policy_change.py` has no filesystem-write call in it at all,
verified directly by a test that inspects its own source. A proposed field outside a fixed,
closed set is refused before being shown, not silently accepted; a proposed value whose quote does
not actually appear in the input text is dropped the same way an invented reply-evidence span is.
Backoff tiers, confidence thresholds and reply-policy thresholds are deliberately outside the set
a circular can touch — those are this project's own risk tolerance, not something NPCI or RBI
states, and a circular is not a mechanism for relaxing them.

**Migrated one module at a time**, full suite run after each, so every value is confirmed
identical to what was previously hardcoded — a change to the *source* of a number, not to the
number — before the next module was touched.

**Not included:** an `--apply` flag, or anything that writes the proposed change for you. Editing
`config/policy.yaml` is a person's action, deliberately, the same way approving a held case is.
Also not included: preserving the file's own comments through an automated edit — a real concern
raised and set aside rather than solved, since the honest answer this late is "not done," not a
tool that silently strips the documentation explaining why each number is what it is.

---

### C9f — A customer paying a different way is a real, observable event
A recovery link that nobody can tell got paid is a nudge, not a recovery.

**Done when:** `create_payment_link` tags the link with the case id (`reference_id`, a documented
Payment Links field); `ingest/webhooks.py` handles `payment_link.paid`, reads that reference back
off the payload, and closes the case — `CaseState.RECOVERED` (defined on the model from the start,
never previously set anywhere in the codebase), every pending retry and nudge cancelled, the real
payment id and amount on the audit record. The scheduler terminal shows it as its own event,
distinct from a routine cancellation.

**Why it exists.** Before this, a customer who paid through the recovery link and a customer who
never saw it looked identical to the system: the retry stayed scheduled either way, and would have
fired against money that had already arrived the moment a real debit adapter exists (see C9d's own
`NO ADAPTER` limitation). This is also the first real, non-modelled signal this project can ever
accumulate toward "how much did this recover" — every other treatment of that question in this
repository is explicitly a ceiling or a range with its assumption on the page, because there was no
real outcome data to measure. A recovered case is one.

**Verified against Razorpay's own documentation**, not assumed: `reference_id` in
`api/payments/payment-links/create-standard.md`, the `payment_link.paid` payload shape in
`webhooks/payment-links.md`. The test fixture is their own published example, not hand-written.

**Not included:** `payment_link.partially_paid`. Reconciling a partial payment against the
mandate's remaining balance is a real question this checkpoint does not answer, and pretending a
partial payment closes a case the way a full one does would misstate what happened.

---

### C9g — Confidence is measured against real outcomes, not just generated ones
Issue #7 asked whether 0.90 and 0.70 are the right cut-points. C8 could not answer it on generated
data, because the correct class there is whatever the generator picked — asking "did the confident
ones do better" of data where confidence never had a chance to be wrong answers nothing.

**Done when:** `unhalted calibration` groups every case with a real terminal outcome — recovered,
unrecovered, or excluded because the customer revoked or it was never really a failure — by the
confidence band its diagnosis fell into, and reports the recovery rate per band, with any band
under 20 cases flagged as too few to conclude anything from rather than presented as a finding.

**Why it exists.** `mark_recovered` (C9f) is the first thing in this project that gives a case a
real, non-generated outcome. Before it, "is confidence calibrated" had no honest answer anywhere in
this codebase — every other place the question comes up says so explicitly and moves on. This is
the seam that becomes real evidence the moment real volume exists, rather than staying permanently
unanswerable.

**Not included:** a conclusion. Today's real volume is a handful of cases, and the report says so
in the same words a small precision figure elsewhere in this project already does — shown, not
hidden, and explicitly not claimed as calibration.

---

### C9h — A taxonomy gap is proposable, not just held
Every case `diagnose()` cannot match sits `UNKNOWN`, held for a human, and until now that was the
end of it — nothing fed the gap back toward closing it.

**Done when:** `scripts/propose_taxonomy_rule.py` clusters held, genuinely unclassified cases by
`(method, error_reason, error_source, error_step)` — plain grouping, no model call — and, for a
chosen cluster, `core/taxonomy_proposal.py` proposes a rule grounded in Razorpay documentation
supplied by the caller: a class, a directness, and a quote checked against the actual text, the
same evidence discipline `policy_change.py` already established. Never writes to
`core/taxonomy.py`.

**Real finding, kept rather than quietly fixed.** The first live test — Razorpay's own
`payment_risk_check_failed` documentation — came back `directness: DIRECT` while the model's own
`rationale` in the same response said the cause "is not communicated." The system prompt already
named this exact failure mode and the model made it anyway. Recorded in `BREAKAGE.md` as the
reason nothing here applies a proposal automatically: a model that can state the correct reasoning
and reach the wrong conclusion in the same breath is not one whose conclusion should be trusted
unread, however carefully the prompt is worded.

**Not included:** applying a proposal. Editing `core/taxonomy.py` is a person's action, the same
distance between recommending and acting every other model call in this project keeps.

---

### C10 — Submission ready
Everything the form asks for exists and agrees with everything else.

**Done when:** the README results table is filled with measured numbers; `BREAKAGE.md` has real
entries written as they happened; the video is recorded and under five minutes; the repo is public;
the filmed commit is tagged `v0.1-demo`; and the form is submitted.

**Serves:** getting hired.

---

## Permanently out of scope

Written down so the question does not get reopened at 2am.

- Any second merchant, tenant, or currency
- Authentication, user accounts, or a login screen
- A production database, migrations, or deployment
- Real voice calls or a real human agent queue
- Live-mode Razorpay keys, under any circumstances
- Regional languages beyond Hinglish and English
- Any refactor that does not unblock the next checkpoint
- Any code path that exists only to make the video work

---

## If we fall behind

Cut in this order. Decided now, in daylight, so it does not have to be decided while panicking.

**Cut breadth, never depth.** Drop whole capabilities and say so. Never ship a capability that
half works — a system that does four things properly beats one that does eight things partly, both
in the repo and on camera.

1. **The generated HTML report** — drop it entirely. `unhalted report` in the terminal shows the
   same numbers from the same tables.
2. **Live WhatsApp** — fall back to the console notifier. Same gating, same parsing, different
   transport.
3. **C7 ladder depth** — drop rung sequencing entirely and keep two rungs that fully work. Keep the
   EV gate and the compliance lint.
4. **C8 batch size** — 300 cases instead of 500. The lift calculation is identical.
5. **C6 breadth, never C6 itself** — support promise-to-pay, dispute precedence, opt-out and
   distress completely. Remove the other intents rather than shipping them unreliable.
6. **C4 volume** — fewer real captured cases, more replay. Say so in the README.

Never cut, at any cost: **C5** (the shell refusing the model), the **holdout in C8**, and **C6**
(reply understanding). The first two are the architectural argument. C6 is the answer to "what does
the AI actually do" — 85% of documented failures are deterministic, so reply parsing is where the
model is genuinely irreplaceable. Cut it and this is a well-built rules engine at an AI buildathon.

---

## Known facts

Recorded as they are established, so no one re-litigates them later.

| Fact | Status |
|---|---|
| Subscriptions dashboard | usable in test mode; Card and eMandate enabled, **UPI cannot be enabled** (NPCI-regulated, needs account activation) |
| Subscriptions and Plans **API** access | **Now working.** Returned 401 on GET and POST all morning on 2026-08-31, including with a regenerated key; began answering later the same day after Card and eMandate were enabled in the dashboard's Subscriptions settings, which appears to provision the API asynchronously. Plans, subscriptions and hosted subscription links all create successfully. Caught by a live API test, not by assumption |
| Orders, Customers, Payments, Tokens, Invoices APIs | all 200 in test mode |
| Razorpay's own retry model (UPI and cards) | T+1, T+2, T+3, then `halted` — automatic, no merchant action |
| Debit timing under Subscriptions | owned by Razorpay; merchant cannot choose it |
| Manual charge of a domestic card under Subscriptions | not supported |
| Payment-method change from UPI | to card only; not UPI or emandate |
| Primary integration | Recurring Payments with mandate tokens, so the shell owns debit timing. This decision originally had two reasons — the Subscriptions API being unavailable, and Razorpay owning the debit schedule under Subscriptions. The first no longer holds; the second always was the stronger one and still stands |
| Subscriptions as a second signal | `subscription.pending` and `subscription.halted` are now obtainable. The ingest layer already has the seam. Not wired yet |
| Unavailable transports | UPI Autopay and Subscriptions API. Their **rules** are implemented and tested; only the adapters are absent. Build the seam, never the stub |
| Holdout baseline | Razorpay's documented T+1/T+2/T+3 retry model, not an invented control |
| API-only path to a failed payment | **none on this account.** S2S card creation returns 403, S2S UPI returns 404. A failed payment requires the hosted checkout |
| Driving the hosted checkout by automation | unreliable — cross-origin iframe accepts synthetic keystrokes once, then ignores edits. Capture is human-in-the-loop |
| Error-scenario cards, **either checkout surface** | **do not produce their documented reason on this account.** Tested twice: the hosted payment-link page (2026-08-31) and Standard Checkout, the embeddable widget the cards are documented for (2026-09-01, `pay_TX7bxkAG4ZrWNO`). Both return generic `payment_failed` / `gateway` whatever card is used. Issue #8, closed as answered |
| Error-scenario cards on the payment-link checkout | **do not produce their documented reason.** The hosted page's mock bank offers only Success/Failure, so every failure returns generic `payment_failed` / `gateway` regardless of card. Verified 2026-08-31 with the `insufficient_fund` card. Those cards are for Standard Checkout, the embeddable widget |
| Captured variety | one real reason obtainable this way. The other documented reasons come from Razorpay's published payloads, labelled as such, and the batch replays both |
| NPCI restricted bands | 10:00–13:00 and 17:00–21:30 IST |
| Scope of the NPCI bands | **UPI Autopay only.** Cards are not NPCI-routed for recurring and emandate settles through NACH. Encoded in `windows.UNBANDED_METHODS`; an unknown method is treated as banded, because a card delayed wrongly costs hours and a UPI debit inside a band is a breach. Fixed in #30 |
| Razorpay's retry model, per method | **Not one model.** UPI is documented in full (T+1/T+2/T+3 then halted) and so are cards ("thrice, once every day for 3 days"). Emandate is not: they state no retry count and say the next attempt waits on the previous one settling, "as it may take more than 24 hours", plus bank-holiday shifting to T-1 or T-3. The emandate count in `baseline.py` is an assumption and is flagged `assumption_used`. Source: `payments/subscriptions/payment-retries.md`, verified 2026-09-03 |
| Charge initiates after pre-debit alert | 25 hours |
| UPI Autopay limits | mandate creation up to ₹1,00,000; frictionless debit ₹15,000, or ₹1,00,000 for BFSI. Above that requires **additional customer authorisation**, not failure |
| Card recurring limit | above the limit, domestic card charges **fail automatically** |
| Emandate limit | ₹1,00,00,000 |
| Ceiling rule | three limits, two different consequences. The shell must know the method before deciding |
| Customer bank balance | not obtainable from any API |
| Holdout on generated data | assigned and reported, but does no work — a control absorbs unobserved variation and two deterministic policies over generated cases have none. Both run over every case instead, which is a paired comparison. The holdout earns its keep against real traffic |
| Retention offers | **not built, by decision.** The project offers nothing, so any offer in a draft was invented and is blocked outright rather than checked against a catalogue |
| Expected-value gate | split. A rung costing more than the whole stake is refused with no assumption, since a probability cannot exceed 1. Anything marginal rests on a success rate that is **merchant policy, not a measurement** — this project cannot measure one, because a generated batch's outcomes are decided by whoever writes them. C8 does not fix that. See issue #10 |
| Documented error reasons | 110 in `errors/payments/list.md`; 16 card and 10 UPI carry root-cause detail |
| Documented ambiguities | exactly 4 of 26 card/UPI reasons have more than one root cause: `card:payment_cancelled`, `upi:credit_failed`, `upi:gateway_technical_error`, `upi:payment_timed_out`. **85% of documented reasons are deterministic by Razorpay's own reference** |
| Ambiguity is method-specific | `payment_timed_out` has 1 documented cause on cards and 2 on UPI |
| Confidence | derived, not chosen: `(1/n documented causes, lifted when error_source selects one) x (DIRECT 1.0 or INFERRED 0.8)`. Generated facts pinned to a Razorpay docs commit |
| Deliberately held | `payment_risk_check_failed` — the bank called it fraudulent. No recovery class fits; re-authorisation would be actively wrong |
| AI core endpoint | OpenRouter, OpenAI-compatible, model `z-ai/glm-5.3-flash` |
| Hinglish extraction quality | verified 6/6 on the spec's own examples, including the three-intent case and the frustration-not-cancellation trap |
| Measured reply-parse cost | ~₹0.01 per reply (\$0.000719 for 6 parses). OpenRouter returns exact `usage.cost` per call, so model spend is measured, not estimated |
| Model endpoint reliability | intermittently returns null content; OpenRouter routes across providers. Retry-on-empty is required, and persistent null must degrade to human review |
| Expected-value gate reachability | the provable half **cannot fire at an entry rung**. The most expensive entry is re-authorisation at ₹2 and the cheapest realistic subscription is ₹49, so cost never exceeds stake. It becomes reachable only on escalation — a ₹60 callback against a ₹49 charge — and the batch cannot escalate, because deciding a rung failed is an outcome model. Exercised by `tests/test_ladder.py`, not by the batch. Issue #15 |
| Model calls on the 300-case batch | **0 of 300.** Every generated failure resolved from the rules table, so inference spend was ₹0.00. That is the measurement, not a missing one — it is the 85% figure appearing as a count. The model's other work needs a customer to reply, and a generated batch has none. Issue #16 |
| Confidence cut-points `0.90` and `0.70` | **policy, not measurement**, said so at the point of definition in `models.py`. Settling them needs ground truth for classification correctness, which generated data cannot supply — the correct class is whatever was generated. What is countable, and reported, is how much the choice matters: 68% land above 0.90, 23% between, 9% below 0.70. Issue #7 |
