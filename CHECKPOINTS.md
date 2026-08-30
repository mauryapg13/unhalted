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

**Done when:** the rules table maps `(error_reason, error_source, error_step)` to a class for every
documented Razorpay card and UPI error reason, unknown strings are forced to `unknown` and held, and
each diagnosis records whether it came from the table or the model.

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
ceiling and the mandate's own `max_amount` are checked before every debit; and all seven stop rules
fire and cancel pending actions atomically.

**Serves:** the 1:45 beat, which is the strongest moment in the video.

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

**Not included:** conditional promises. The clarification loop. Regional languages beyond Hinglish.

---

### C7 — Escalation is gated
Interventions are chosen and are stoppable on cost.

**Done when:** the ladder selects an entry rung from the diagnosis; the expected-value gate
terminates a low-value case as uneconomic with the calculation logged; and a drafted message
containing an invented discount is blocked by compliance lint and regenerated.

**Serves:** the 2:55 beat, and the answer to "isn't this more expensive than the money you recover".

**Not included:** real voice calls. Real human callbacks. Both are simulated.

---

### C8 — The number exists and is honest
Recovery is measured against a control, not asserted.

**Done when:** a batch of at least 300 cases runs to completion; a 10% holdout receives baseline
behaviour only; the report shows recovery per diagnosis class, recovery per rung, stop-rule counts,
intervention spend, model spend, and **lift as agent cohort minus holdout**; and false-failures are
reported in a separate line that is not counted as recovery.

**Serves:** the 3:05 beat, and the track's stated bar.

**Not included:** statistical significance testing. Multi-month simulation.

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

**Optional, last, only if C10 is otherwise met:** `unhalted report --html` writing a static file
from the same measurement tables. A generated report, not a dashboard — no server, no live wiring.

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
5. **C6 breadth** — support promise-to-pay, dispute precedence and opt-out completely. Remove the
   other intents rather than shipping them unreliable.
6. **C4 volume** — fewer real captured cases, more replay. Say so in the README.

Never cut, at any cost: **C5** (the shell refusing the model) and the **holdout in C8**. Those two
are the entire argument. A demo without them is a demo of something anyone could have built.

---

## Known facts

Recorded as they are established, so no one re-litigates them later.

| Fact | Status |
|---|---|
| Subscriptions dashboard | usable in test mode; Card and eMandate enabled, **UPI cannot be enabled** (NPCI-regulated, needs account activation) |
| Subscriptions and Plans **API** access | **401 on GET and POST — product not entitled on this account.** Bare `{"error":"Unauthorized"}`, not Razorpay's structured error, so it is rejected before reaching the service. Orders returns 200 in the same session. Not fixable in code |
| Orders, Customers, Payments, Tokens, Invoices APIs | all 200 in test mode |
| Razorpay's own retry model (UPI and cards) | T+1, T+2, T+3, then `halted` — automatic, no merchant action |
| Debit timing under Subscriptions | owned by Razorpay; merchant cannot choose it |
| Manual charge of a domestic card under Subscriptions | not supported |
| Payment-method change from UPI | to card only; not UPI or emandate |
| Primary integration | Recurring Payments with mandate tokens, so the shell owns debit timing |
| Unavailable transports | UPI Autopay and Subscriptions API. Their **rules** are implemented and tested; only the adapters are absent. Build the seam, never the stub |
| Holdout baseline | Razorpay's documented T+1/T+2/T+3 retry model, not an invented control |
| `initiate_payment` + `submit_otp` can produce API-only failures | not yet checked |
| NPCI restricted bands | 10:00–13:00 and 17:00–21:30 IST |
| Charge initiates after pre-debit alert | 25 hours |
| UPI Autopay limits | mandate creation up to ₹1,00,000; frictionless debit ₹15,000, or ₹1,00,000 for BFSI. Above that requires **additional customer authorisation**, not failure |
| Card recurring limit | above the limit, domestic card charges **fail automatically** |
| Emandate limit | ₹1,00,00,000 |
| Ceiling rule | three limits, two different consequences. The shell must know the method before deciding |
| Customer bank balance | not obtainable from any API |
| AI core endpoint | OpenRouter, OpenAI-compatible, model `z-ai/glm-5.3-flash` |
| Hinglish extraction quality | verified 6/6 on the spec's own examples, including the three-intent case and the frustration-not-cancellation trap |
| Measured reply-parse cost | ~₹0.01 per reply (\$0.000719 for 6 parses). OpenRouter returns exact `usage.cost` per call, so model spend is measured, not estimated |
| Model endpoint reliability | intermittently returns null content; OpenRouter routes across providers. Retry-on-empty is required, and persistent null must degrade to human review |
