# Working agreement

`unhalted` is a mandate-recovery agent for Razorpay. The model diagnoses; a deterministic
shell decides. See README.md for the architecture and CHECKPOINTS.md for scope.

## Scope

**CHECKPOINTS.md decides what counts as done.** Work the lowest unmet checkpoint. Do not
start the next one until the current one's "Done when" line is literally true. If something
seems missing from that file, say so and wait — do not build it and ask afterwards.

Nothing exists only for the demo. No `DEMO_MODE` branches, no canned responses, no screen
reading fixtures while presenting itself as live. Anything unfinished is absent, not stubbed.

## Git

- **Never commit to `main`.** Work happens on short-lived branches — `feat/`, `fix/`,
  `spec/`, `docs/`, `spike/` — that reach the trunk by pull request. A pre-commit hook
  enforces this; do not use `--no-verify` to get past it.
- Finish a piece of work with **`/wr`**, which updates `CHANGELOG.md`, commits and pushes.
  Do not hand-roll commits in place of it.
- Conventional commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `chore:`. `/cl` audits
  the changelog against them.
- Never `git add .` or `git add -A`. Stage what you changed.

## Facts

**Verify against Razorpay's documentation before deciding anything.** Their docs source is
`github.com/razorpay/markdown-docs`, which is more reliable than search summaries. Settled
facts live in the Known facts table at the bottom of CHECKPOINTS.md — read it before
re-litigating something, and add to it when you establish something new.

Do not assert an API's behaviour from memory. Check it, or probe it.

## Failures

Log real breakages in `BREAKAGE.md` as they happen, not reconstructed later. What happened,
why, and what changed as a result. This is a submission deliverable, not housekeeping.

## Commands

```bash
uv run pytest                  # the specification suite
uv run ruff check .            # lint the whole tree, not just src/
python3 scripts/preflight.py   # verify Razorpay and the model endpoint
```
