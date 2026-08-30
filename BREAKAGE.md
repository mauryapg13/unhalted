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
