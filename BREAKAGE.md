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
