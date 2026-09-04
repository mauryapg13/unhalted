# What broke

A running log of development failures, what caused them, and what changed as a result.
Written as things break, not reconstructed afterwards.

---

### The specification was not valid Gherkin
**Date:** 2026-08-31

**What happened:** The first test written against the spec — one that only parses the feature files
and checks they contain scenarios — failed on six of the seven files immediately.

**Why:** The specification was written as a design document before it was ever executed, and long
steps were wrapped across several indented lines for readability. That reads well in Markdown, but
Gherkin steps must be a single line unless they use a docstring or a data table. Nobody had ever run
the parser over it, so nobody knew. I then made it worse: the correction adding NPCI's second
restricted band followed the same wrapped style and introduced a seventh broken file.

**What changed:** Wrapped continuation lines are now joined into single steps, verified by
`tests/test_specification.py`, which runs in CI. Two lessons kept:

1. A specification nobody has executed is a document, not a contract. It was treated as settled for
   several days while being unparseable.
2. The first test written should be the one that validates the foundation everything else assumes —
   not a test of the first feature built. Nine tests that parse the spec caught a problem that would
   otherwise have surfaced halfway through implementing step definitions, with far less clarity
   about the cause.

---

### The model endpoint returned nothing, twice, for no visible reason
**Date:** 2026-08-31

**What happened:** The first Hinglish extraction probe crashed on its second case with
`'NoneType' object has no attribute 'strip'`. The HTTP status was 200, the response was
well-formed, and `choices[0].message.content` was `null`.

**Why:** Two causes, found by dumping the raw response instead of guessing. The first occurrence was
self-inflicted — `max_tokens: 32`, too small for the model to produce anything. The second was not:
OpenRouter routes a single model across providers, and the same request was served by DeepInfra on
one call and Z.AI on another. Null content appears to be routing variance. It did not recur across
six subsequent calls.

**What changed:** Empty content is now treated as an expected outcome rather than an exception. The
AI core retries on empty, and a persistent null degrades to a failed parse that the shell routes to
human review — which is the behaviour the specification already required for a low-confidence parse.
An intermittent fault that appears once in seven calls is exactly the kind that survives to
production, so it is handled now rather than after it appears in a demo.

---

### I stopped following my own workflow, and only noticed when asked
**Date:** 2026-08-31

**What happened:** After writing a branching model — short-lived branches, squash-merge via
pull request, `main` only accumulating reviewed states — I committed straight to `main` twice,
left an entire checkpoint's work uncommitted on the trunk, and never once ran the `/wr` command
that exists to keep `CHANGELOG.md` current. None of it was noticed until the drift was pointed
out directly.

**Why:** Two causes. The rules lived only in a conversation and in a document nothing enforced,
so following them depended entirely on remembering to. And `/wr` itself contained a rule —
"stop if the branch is not `main`, do not push from anywhere else" — that directly contradicted
the branching model, so following either one meant violating the other. That contradiction was
never surfaced; it was silently resolved by doing neither properly.

**What changed:** The rules moved out of prose and into mechanism, in three layers:

1. `.githooks/pre-commit` refuses any commit on `main` and warns when no `CHANGELOG.md` change
   is staged. Installed by `core.hooksPath`, so it applies to every tool, not just one.
2. A Claude Code `PreToolUse` hook (`scripts/hooks/commit-guard.sh`) denies the same thing
   earlier, before the command runs.
3. `CLAUDE.md` states the workflow so it loads into context every session.

`/wr` was also reconciled with the branching model rather than left contradicting it.

Proving the git hook worked found a second bug: branch detection used
`git rev-parse --abbrev-ref HEAD`, which returns `HEAD` rather than the branch name on a
repository with no commits, so the guard silently failed open and let a blocked commit through.
`git symbolic-ref --short HEAD` is correct. A guard that fails open is worse than no guard,
because it is trusted.

**The lesson, which is the project's own thesis pointed inward:** this system is built on the
premise that a probabilistic component cannot be trusted to follow rules, so the rules are
enforced in deterministic code it cannot route around. I spent an afternoon being the
probabilistic component that drifted from its own rules. The fix was not a better intention. It
was a shell.

---

### The service never read its own configuration
**Date:** 2026-09-01

**What happened:** With the webhook registered and the tunnel live, the first three deliveries
from Razorpay were all refused: `503 — RAZORPAY_WEBHOOK_SECRET is not set`. The secret was in
`.env`, where it had been written twenty minutes earlier.

**Why:** Nothing loaded `.env` into the application. `scripts/preflight.py` parsed the file
itself, and `scripts/demo.sh` passed the variables inline on the command line, so every path that
had ever been exercised supplied the environment by some other means. The service, which is the
only thing that needs it in production, had no way to read it at all. It would have refused every
webhook Razorpay ever sent.

**What changed:** `src/unhalted/config.py` loads `.env` at import, never overwriting a real
environment variable, so deployment without a `.env` behaves correctly. The webhook endpoint and
the store read through it.

Two things worth keeping:

1. **The failure mode was right even though the configuration was wrong.** The endpoint returns
   503 and refuses rather than accepting unsigned webhooks — an endpoint that accepts those is one
   anyone can open cases on. It failed closed, loudly, with the reason in the log.
2. **Razorpay redelivered.** The payment whose webhook was refused arrived again once the fix was
   in, and opened its case normally. Their retry behaviour recovered the event with no
   intervention — which is exactly why the endpoint acknowledges events it does not handle rather
   than rejecting them.

**The lesson:** every path that exercised the configuration did so in a way production never
would. Tests passed, the demo passed, preflight passed — and the one caller that mattered had
never been tried. It took a real webhook from a real Razorpay to find it.

---

### The specification has nine stop rules; I wrote seven down and repeated it for two days
**Date:** 2026-09-01

**What happened:** `CHECKPOINTS.md` said "all seven stop rules" from the day it was written, and
the README said the same. The specification's Scenario Outline defines nine: `REVOKED`, `OPT_OUT`,
`DISPUTE`, `DISTRESS`, `RETRY_CAP`, `LADDER_END`, `CHARGEBACK`, `MERCHANT_PAUSE`, `REG_HOLD`.

**Why:** I counted the rules that halt customer contact and missed `RETRY_CAP` and `LADDER_END`,
which end a phase of recovery rather than silencing it. Then the wrong number was quoted back in
every summary for two days without anyone recounting, including by me.

**What changed:** Corrected everywhere, and `tests/test_stops.py` now asserts the exact set of nine
codes against the specification rather than a count I remembered. A number in prose is a number
nobody checks; a number in a test is one that cannot drift.

**The lesson:** the same one as the Gherkin that did not parse. Anything asserted in a document and
not asserted in a test will eventually be wrong, and will be repeated confidently until something
executes it.

---

### I called C5 done while the ceiling check had no caller
**Date:** 2026-09-02

**What happened:** C5's "Done when" requires that the ₹15,000 ceiling and the mandate's own
`max_amount` are "checked before every debit". `shell/limits.py` was written, had twelve passing
tests, and was called by nothing. I reported the checkpoint complete, twice, and only looked when
asked directly whether it was finished.

**Why:** I verified the module worked rather than verifying the requirement was met. Those are
different questions and the difference is the whole point of the checkpoint being worded as
behaviour rather than as a component. A tested module nobody calls is decoration — the exact
failure mode CHECKPOINTS.md warns about when it says anything unfinished is absent rather than
stubbed. This was worse than a stub: it looked finished from every angle except the one that
mattered.

**What changed:** `limits.check` now runs in `handle_failure` before any retry is scheduled, and
four tests assert it from the agent's entry point rather than from the module. A debit above the
card ceiling, above the mandate's ceiling, or above UPI's frictionless limit is refused with its
reason in the audit trail, and the case does not get a retry.

`FailureSignal` gained `mandate_max_paise` for the consent check. It is `None` until the token is
fetched, and the field says so at its definition: the network ceilings still apply, the consent
check cannot run, and the audit record shows which of the two was evaluated.

**The lesson:** "the tests pass" is not "the requirement is met". The next time a checkpoint says
a rule is enforced, grep for the caller before saying it is done.

---

### The hooks stopped me committing wrongly; nothing stopped me not committing
**Date:** 2026-09-02

**What happened:** The whole of C6 — the parser, the policy layer, the labelled corpus, the
evaluation, both terminals — sat as sixteen uncommitted files with **zero commits** on the branch.
A machine failure would have lost all of it. Noticed only when asked directly whether I was
following the repository's own commands.

**Why:** The mechanisms added on 2026-08-31 all fire *at* a commit: the pre-commit hook refuses
`main`, the commit-msg hook refuses a missing changelog, CI refuses a pull request without one.
Every one of them is a gate on the act of committing. None of them can observe the absence of
commits, so a long stretch of work with no commit at all passes through untouched.

It is the same shape as the ceiling check with no caller: the guard was correct and was never
reached.

**What changed:** Nothing mechanical, and that is the honest part. A hook cannot fire on an event
that does not happen. What would catch it is a periodic check — uncommitted files older than an
hour, or a branch with no commits and a dirty tree — and that is a background job, not a hook.

Recorded here rather than fixed, because a fix invented under embarrassment is worse than a
limitation written down. The working rule for now is that a checkpoint's work gets committed when
the checkpoint's tests pass, not when the checkpoint is finished.

**The lesson:** enforcement covers the paths you thought of. The first drift was doing the wrong
thing, and a hook catches that. The second was doing nothing, and nothing catches that.

---

### Three stop rules, three different ways of not stopping
**Date:** 2026-09-02

**What happened:** Three separate bugs, found on three separate days, all in the path between
deciding to stop and actually stopping.

1. A customer replying "cancel my subscription" had the case routed to a human, and **the
   scheduled retry stayed armed**. Found by the user asking, mid-test, why it was still retrying.
2. A dispute halted the case without holding it for a human. Halting is not routing; the case
   would have sat closed with nobody looking at it. Found by the user asking whether disputes
   should go to a person.
3. `unhalted case` printed `pending 0 automated action(s)` directly beneath a timeline saying a
   retry was scheduled for 13:00. Both lines were true. The retry was recorded in the audit trail
   as a decision, and never recorded as pending work — so `apply_stop` had nothing to cancel and
   cancelled nothing. **A customer revoking their mandate would still have been charged.**

**Why:** The same shape every time. Each individual piece was correct — the stop rules fired, the
audit trail recorded, the CLI reported honestly. What was missing was the wiring between them, and
a unit test that exercises one piece cannot see a seam that does not exist. Every test passed
through all three.

The third is the clearest: the system had a diary and a calendar. It wrote "charge them at 1pm" in
the diary, which nothing reads, and never put it on the calendar, which is what gets cancelled.

**What changed:**

- Cancellation now cancels pending actions, under a stated rule: *nothing automated may remain
  scheduled on a case a person now owns.*
- `DISPUTE` and `CHARGEBACK` carry `terminal_state=HELD_FOR_HUMAN` rather than halting.
- A scheduled retry is written to `pending_actions`, not only to the audit trail, with a
  regression test in `tests/test_stops.py`.

**The lesson, and the reason these are one entry:** all three were found by *using* the system —
two by the user typing into it, one by reading its own output — and none by the 246 tests. Tests
check that a part works. Only operating the thing checks that the parts are joined. The C9 CLI
paid for itself before it was merged, which is an argument for building observability early rather
than last.

**Written late, and worth saying so.** These were fixed as they were found, but logged here in one
pass afterwards rather than at the moment each broke — a smaller version of the drift recorded
above. The pattern only became visible once there were three of them.

---

### The null responses were never the provider's fault
**Date:** 2026-09-03

**What happened:** An exploratory pass over the reply parser — adversarial inputs, not the labelled
corpus — found that replies carrying more than one intent fail to parse **two times in three**. The
specification's own three-intent example is one of them. A plain promise never fails.

**Why:** `max_tokens: 1200`. `z-ai/glm-5.3-flash` is a reasoning model; it spends the completion
budget thinking before it emits anything. When the budget runs out first, the response arrives with
`finish_reason: "length"` and a `content` field that is either truncated mid-JSON or **empty**.
Measured at three runs per reply, temperature 0: 1 of 3 parse at 1200, 3 of 3 at 4000, where the
model stops on its own at about 971 tokens.

**What this corrects.** The entry above — *The model endpoint returned nothing, twice, for no
visible reason* — concluded that the second occurrence was OpenRouter routing one model across
providers that behave differently. That conclusion was wrong, or at best incomplete. The symptom
was the same one measured here, and the response said so on every call: `finish_reason` was
`length`, and nothing in the code has ever read it.

The first occurrence in that entry was diagnosed correctly — `max_tokens: 32`, too small. Having
found the cause once, I raised the number until the symptom went away and attributed the rest to
the provider. 1200 was large enough for the replies being tested that day and too small for the
ones the product is for.

**What changed:** Nothing yet — raised as issue #22 rather than fixed mid-investigation. The fix is
a larger budget, reading `finish_reason`, and not retrying a `length` failure, which at temperature
0 reproduces exactly and only triples the spend. `docs/reply-evaluation.md`'s 1-in-68 failure rate
was measured on a corpus that is mostly simple replies and understates this.

**The lesson:** a cause found is not the cause. The first `max_tokens` bug made the second one
invisible, because the fix for one was the mask for the other — and "intermittent, provider-side"
is a comfortable explanation that costs nothing to believe and closes an investigation. The
response body carried the answer the whole time in a field nobody had thought to look at.

**The wider lesson, and the reason this pass happened at all:** every bug in this file was found by
using the system, and every one of these was found by using it *the way somebody else would*. The
suite tests the replies we wrote. Nobody had sent it three emoji, a 6,000-character message, pure
Devanagari, or a reply that quotes the parser's own output schema back at it.

---

### The lease read its own claim back by a key that was not unique
**Date:** 2026-09-03

**What happened:** Asked whether one worker would really be enough at scale, I tested it instead of
arguing: four OS processes leasing from one SQLite file. **386 of 400 actions were claimed twice**,
and one worker reported claiming 1,470 of the 400 that existed.

**Why:** `lease_due_actions` did the claim and the read as two statements — `UPDATE ... SET
leased_until = ?, worker = ?` and then `SELECT ... WHERE worker = ? AND leased_until = ?`. That
pair is not a unique key. A worker claiming twice inside one lease window computes the same
`leased_until` both times, so its second read returns the first batch as well, cumulatively. Two
workers whose clocks agree collide outright.

Every duplicate would have been a debit attempted twice.

The module's own docstring says, in the paragraph directly above the bug: *"selecting first and
updating after is how the same retry gets handed to two workers; this repository has already
shipped one check-then-act race and does not need a second."* I wrote that, and then wrote the
race, because I was thinking about the ordering of the two statements and not about whether the key
joining them identified anything.

**What changed:** `UPDATE ... RETURNING *`. Claim and read are now one statement, so there is no
key to get wrong. Four processes, 400 actions, zero double-claims, and a test in
`tests/test_store_concurrency.py` that runs real subprocesses — threads share this process's Store
and its lock, so they cannot show what two deployed workers would do.

Also added `PRAGMA busy_timeout = 5000`. SQLite fails a locked write immediately by default, and
with several workers a claim will sometimes arrive while another holds the write lock. That test
passed without it only because the contended window is short.

**The lesson:** a comment describing a hazard is not a defence against it. The docstring named this
exact failure and sat six lines above the code that committed it. What caught it was somebody
asking a sceptical question about scale, and the decision to answer with a test rather than an
argument — which took four minutes and would have taken a production incident otherwise.

---

### A test of a mock passed locally by calling something real
**Date:** 2026-09-03

**What happened:** CI failed `test_a_truncated_response_is_not_retried` — `assert calls["n"] == 1`
got `0`. It had passed locally, including in the same run that produced this file's entry above.

**Why:** the test replaces `httpx.post` and expects `_call_model` to reach it. `_call_model`
returns before that line whenever `config.model_api_key()` is empty: `if not key: return
ModelCall(None, "no model API key configured", 0)`. Locally `.env` carries a real key, so the guard
passed and the mock got exercised; CI has no such file, the key is empty, and the mock sat unused
while the function returned its own "not configured" result — which still happens to satisfy
`parsed.failed` and the general shape of the assertion, just not the count that was the point of
the test.

**What changed:** the test now sets `config.model_api_key` to a fake value itself, so what it
exercises no longer depends on which machine it runs on.

**The lesson:** a test that reaches a real config function instead of a fixture is a test of
whatever machine happens to run it. It passed here for the same reason a bug can hide behind a
default that's only ever true in one place — nothing forced the gap into view until a second
environment actually differed from the first.

---

### A promise for tomorrow morning landed 24 hours later instead
**Date:** 2026-09-03

**What happened:** replying "kal subah" (tomorrow morning) to a card retry produced a schedule
labelled 23h 59m out — the customer's next day, but at whatever minute the reply happened to
arrive, not morning at all. Caught live, on the scheduler terminal, watching a real rehearsal.

**Why:** `validate_date` deliberately reduces a promise to a `date`, not an instant — "the 2nd" has
no time of day, and asking the model to invent one would be fabricating what the customer said.
Realignment then combined that bare date with `now.timetz()` — the clock reading at the instant the
reply was parsed. A promise made at 21:24 landed at 21:24 the next day; a promise made at 09:00
would have landed at 09:00. The day was right and always incidental; the time was never anything
the customer stated, only ever whatever the shell's own clock said back to itself.

**What changed:** the promised day now combines with `windows.CONTACT_OPEN` (08:00 IST) — the start
of contact hours, already the codebase's own answer to "what time does a day begin" via
`next_allowed_contact`. A promise for "the 2nd" now lands at the start of the 2nd, every time,
regardless of when in the conversation it was made.

**The lesson:** a value that is only *usually* stable — here, the clock reading at parse time —
reads as a constant until the one day it visibly isn't. Nothing about the realignment tests caught
this because none of them varied `now`'s time of day; they only varied the promised date, so the
line reusing the wrong half of `now` never had a reason to disagree with itself.

---

### A redelivered payment was scheduling a second, real retry
**Date:** 2026-09-04

**What happened:** building `scripts/inject.py` — a way to run one documented scenario through
the real pipeline twice, to prove re-running the same one matched back to the same case rather
than duplicating it — the case matched correctly, and had **two** pending retries. Reproduced the
same thing directly against the real webhook endpoint: one payment, two event ids, one case,
two scheduled debits. Every case in `pending_actions` for a redelivered failure was carrying an
extra live retry nobody had asked for.

**Why:** `handle_failure` gated "should I diagnose and schedule this" on `open_case_or_get`'s
`created` flag — true only for the call that inserted the case row. That flag answers "did I just
create this row", which is a different question from "has this signal actually been worked yet",
and the gap between the two was invisible until `ingest/webhooks.py`'s own architecture was
considered: it calls `store.open_case(signal)` itself, for durability, *before* it ever calls
`handle_failure` — so by the time `handle_failure` runs, the row already exists regardless of
whether this is a payment's first delivery or its fifth. `created` reads False every time through
the real endpoint, on purpose, for a reason that had nothing to do with redelivery.

That also meant the audit trail's own "case-opened" vs "signal already known" wording — added in an
earlier pass specifically to stop the trail asserting an event that never happened — was wrong in
the other direction the whole time: every genuine first-ever webhook delivery was being recorded
as "signal already known", because the row already existed by the time the wording was decided.
Nothing caught it because the tests written for that fix drove `handle_failure` directly, the way
`scripts/session.py` does, never through a caller that pre-creates the case the way the real
endpoint does.

**What changed:** the gate and the wording are now the same question, asked once: does this case
already have a diagnosis on record. That is true only once the agent has actually decided something
about a case, regardless of which caller happened to insert its row first, so it correctly reads
"case-opened" on a genuine first delivery even when `webhooks.py`'s durability step created the row
a moment earlier, and correctly stops before diagnosing or scheduling again on an actual repeat.

**The lesson:** "did this call create the row" and "has this thing been processed" look like the
same fact right up until a second caller creates the row for a different reason first. The
durability pre-creation in `webhooks.py` was added for an honest reason — a signal on disk before
slow work starts — and nobody re-checked what it did to a flag a different function was reading
for an unrelated decision three calls away. Found the same way the double-claim bug was: by
actually running the scenario the architecture was supposed to handle, not by reasoning about it.

---

### The model's completion budget was mostly invisible
**Date:** 2026-09-04

**What happened:** asked to diagnose why a live model call felt slow, a trivial probe — "reply
with ONLY the digit 1" — returned no content at all, `finish_reason: length`, at `max_tokens: 10`.
A realistic call timed at 11-36 seconds across three otherwise-identical requests at
`temperature: 0`.

**Why:** `z-ai/glm-5.3-flash` reasons before it answers, and that reasoning is billed to the same
completion budget as the visible output. Dumping `usage.completion_tokens_details` instead of
guessing showed **85-95% of every completion spent on reasoning nobody sees** — 887, 1309, and 979
tokens across the three timed calls, for 116-171 tokens of actual JSON. The reasoning length is not
pinned by temperature the way visible-token selection is: it varies call to call on identical
input, and that variance — not network routing, one provider (Z.AI) served every call — is what
produced the wide latency swings reported live.

**What changed:** `reasoning: {"effort": "low"}` on every live call. Verified before applying it,
not after: the 7 hardest multi-intent replies in `tests/fixtures/replies/labelled.json` all parsed
identically or better (one case where default effort added a spurious intent that low effort
didn't), 3-9x faster. The full 68-reply evaluation was then re-run under it — cache deleted first,
since its key is derived only from the prompt text and would have silently served pre-change
results otherwise — and `docs/reply-evaluation.md` now carries genuinely current numbers: 68/68
parsed (was 67/68), compliance-critical recall (`opt-out`, `distress`) still 1.00, cost per parse
roughly halved.

**The lesson:** a model that reasons is not slow because of what it says; it is slow because of
what it doesn't show you, and that budget can be entirely consumed with zero visible symptom
beyond an empty response — the ten-token probe above returned nothing and gave no hint why until
the usage breakdown was actually read. "Reasoning models are slower" is true and also not
specific enough to fix anything; the actual lever was one parameter, found by measuring rather
than accepting the latency as a property of the model nobody could do anything about.

---

### A wait with nothing on screen looks exactly like a hang
**Date:** 2026-09-04

**What happened:** `scripts/propose_policy_change.py`, run with no `--file` and nothing piped in,
sat with no output for 30 minutes. Reported by the person actually running it — not a test, a real
terminal, a real pasted command, and a real "is this stuck?".

**Why:** with no `--file`, the script falls to `sys.stdin.read()`, which is correct when input is
piped in but blocks silently waiting for `Ctrl-D` when the terminal is interactive and nothing has
been redirected. Nothing printed anything first. This is the identical shape of bug the reviewer
terminal had — a wait that does not announce itself is indistinguishable from broken — just found
on a script built the same week as that fix, by not applying the lesson to the new code.

**What changed:** `sys.stdin.isatty()` is checked before reading; on a real terminal it prints what
it is waiting for and how to finish (`Ctrl-D` on its own line) before blocking. Piped input and
`--file` are unaffected — confirmed directly, not assumed, by a test that fakes `isatty()` both
ways and a test proving `--file` never touches `stdin` at all.

**The lesson:** the reviewer-hang fix earlier this session was treated as a fix for the reviewer,
not as a rule for anything that waits on input. It should have been the second: "a wait says so" is
a property every interactive entry point needs, not a patch for the one place it was first noticed.

---

### A settled fact never made it back to the README
**Date:** 2026-09-04

**What happened:** answering a live demo question about why every real webhook diagnoses the same
way, `README.md`'s own "Honesty about the data" section — the one section whose whole job is to
say what's real — claimed Razorpay's error-scenario test cards "yield specific documented error
reasons rather than generic failures." Issue #8 had already tested that, twice, and closed it
answered negatively.

**Why:** the finding went into `CHECKPOINTS.md`'s Known facts table and the issue itself, both the
right places — but the README paragraph making the opposite claim was written before the test ran
and nobody came back to it afterward. Two documents disagreed with each other and nothing checked.

**What changed:** the README now states what issue #8 actually found. `CHECKPOINTS.md` was already
correct and needed no change — this was purely the README lagging behind a fact that had already
been settled elsewhere.

**The lesson:** "recorded in CHECKPOINTS.md so nobody spends another twenty minutes on it" (issue
#8's own closing line) prevents re-litigating a question. It does not prevent a different document
from quietly going on asserting the answer that question replaced. A fact settled once needs
checking against everywhere it was claimed, not just recording once where it was settled.

---

### The taxonomy proposer's prompt warned against the exact mistake it then made
**Date:** 2026-09-04

**What happened:** the first live test of `scripts/propose_taxonomy_rule.py` — Razorpay's real
`payment_risk_check_failed` documentation, "Payment declined due to risk checks... The source
parameter would give additional clarity where the risk check failed" — came back `directness:
DIRECT`. Its own `rationale` field, in the same response, said the specific cause "is not
communicated." Those two statements contradict each other, and the model produced both at once.

**Why:** the system prompt already says, in as many words: *"Never claim 'direct' for a reason
their text says is ambiguous or unattributed."* This text is exactly that — Razorpay names three
possible sources (Razorpay, Gateway, Issuer Bank) and says a separate parameter is needed to know
which — and the model classified it as directly stated anyway, then wrote a rationale that
correctly described why it shouldn't have.

**What changed:** nothing in the prompt, yet. This is not a bug fixed by rereading the instruction
harder — the instruction was already there, correctly worded, in the one response that violated
it. What changed is confidence in why this project never lets a proposal write itself: a human
reading this specific output would catch the contradiction between the field and its own
justification in about two seconds, which is a much easier check than getting a model to reliably
avoid making it in the first place.

**The lesson:** a system prompt naming a failure mode is not the same as preventing it, the same
lesson `BREAKAGE.md` already recorded once for a docstring naming a race condition six lines above
the code that committed it. The difference here is that this was always the plan — `propose()`
never writes to `core/taxonomy.py`, precisely because a model that can state the correct reasoning
and still reach the wrong conclusion in the same breath is not a model whose conclusion should be
trusted unread.

---

### The ladder's escalation had never actually run through `run_due`
**Date:** 2026-09-04

**What happened:** a live demo injected `authentication_failed` (classifies `NOTIFICATION_GAP`,
enters the ladder at `NUDGE`), expecting a customer message with a pay link. `unhalted case`
showed the nudge scheduled correctly on paper, but `unhalted run-due` reported `claimed=0` — the
row was never picked up at all.

`agent.py`'s escalation path scheduled every non-`SILENT_RETRY` rung with
`store.schedule_action(case.id, case.customer_ref, ladder.LADDER[rung].name, None, now)`. Two
faults, either one sufficient alone: `kind` was `Intervention.name` — prose meant for a human
("message with a pay link", "re-authorisation link") — which never matched any key in
`runner.EXECUTORS` (`"nudge"`, `"retry"`); and `scheduled_for` was passed as `None`, which can
never satisfy the claiming query's `scheduled_for <= now`. The row existed, looked plausible in
`unhalted case`, and could never be claimed by the real asynchronous runner for either reason
independently of the other.

**Why:** `scripts/session.py` has its own separate, hardcoded call —
`store.schedule_action(case.id, signal.customer_ref, "nudge", now, now)` — that bypasses the
ladder's naming entirely and was the only path ever exercised in a demo. `tests/test_ladder.py`
asserted the escalation *row* looked right (`kind == "re-authorisation link"`) but nothing asserted
the row was *claimable*, so a test locked in the broken kind string as correct, the same pattern
this file has flagged before. The result: every "the pay link generates" claim made earlier this
session was only ever true via `session.py`'s synchronous shortcut, never via the durable
`run_due`/`scripts/schedule.py --run` path the architecture is actually built around.

**What changed:** `ladder.py`'s previously-private `_SLUG` map (`Rung` → plain slugs matching
`runner.EXECUTORS`'s keys: `"nudge"`, `"reauthorisation"`, etc.) is now public `SLUG`, and
`agent.py` schedules with `store.schedule_action(case.id, case.customer_ref, ladder.SLUG[rung],
now, now)` — a real due time, matching how a retry is scheduled, and a kind that actually matches
an executor's lookup key. `NUDGE` now genuinely executes end to end. `REAUTHORISATION`,
`VOICE_CALL` and `HUMAN_CALLBACK` still have no registered executor — scheduling them now
correctly reports "no executor registered for 'reauthorisation'" instead of *also* being
unschedulable, which is the honest absence this project's own rule calls for, not a stub. Added
`tests/test_ladder.py::test_a_notification_gap_nudge_actually_runs_through_run_due`, which asserts
`report.claimed == 1` and `report.done == 1` — a claim the older test's `kind`-string assertion
could pass while the bug was still present.

**The lesson:** a scheduled row that reads correctly in a case listing is not evidence it can ever
be claimed. The only test that would have caught this is one that runs the claiming query, not one
that inspects the row's fields — the same gap between "looks right" and "was run" that this file's
webhook-redelivery and stdin-hang entries already found in other parts of the pipeline.
