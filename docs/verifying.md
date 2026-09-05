# Verifying this system

Everything below was run on 2026-09-05 and the output described is what actually appeared.

There are two levels here. **Level 1 needs nothing but the repository** — no accounts, no keys, no
network — and proves the pipeline, the rules and the whole test suite. **Level 2 needs a Razorpay
test account** and proves the same code against their live API.

Start at level 1. It answers most questions on its own.

---

## Level 1 — no credentials required

### Install

```bash
uv sync
```

Python 3.11 or newer. [`uv`](https://docs.astral.sh/uv/) handles the rest.

### The whole suite

```bash
uv run pytest
```

**538 passed, 5 deselected.** The 5 are marked `live` and are skipped without credentials; level 2
runs them. No network is touched.

```bash
uv run ruff check .
```

### One failure, end to end

```bash
./scripts/demo.sh
```

Self-contained: starts its own server on a temporary database with its own webhook secret, then
posts three requests to it.

1. A **forged** webhook — rejected, no case created
2. The **real** one, signed with HMAC-SHA256 — a case opens and is diagnosed
3. The **same event id again** — recognised as a redelivery, no second case, no second retry

Then prints the case, the diagnosis with its stated reasoning, and the full timeline. Cleans up
after itself; your own database is never touched.

This is the fastest honest answer to "does it work". Nothing in that path is faked — the signature
check, the idempotency check, the normaliser and the taxonomy all run.

### Walk the pipeline, one scenario at a time

```bash
uv run python scripts/session.py --scenario insufficient_fund
```

A conversation in the terminal. The transport is a console instead of WhatsApp; **the gating above
it is identical** — same contact hours, same ceilings, same stop rules, same reply parser.

Reply as the customer and watch what changes:

| Type this | What should happen |
|---|---|
| `next week thursday try karo` | Hinglish parsed, retry realigned to that date, nudges suspended until then |
| `STOP` | `STOP_RULE:OPT_OUT`, every pending action cancelled |
| `actually, i want to continue` | `CONTACT_SUPPRESSED:OPT_OUT` — refused *before the model is called* |

That third line is the one worth checking. A stop here outlives the queue it emptied; nothing the
customer later says lifts it. Only `unhalted lift-stop <id> --by <name>` does.

The five scenarios, and what each should do:

| `--scenario` | Diagnosis | Confidence | Entry rung | Message? |
|---|---|---|---|---|
| `insufficient_fund` | recoverable-balance | 1.0 | 2 — ask for a date | yes |
| `gateway_technical_error` | recoverable-technical | 1.0 | 1 — silent retry | **no** |
| `card_declined` | recoverable-technical | 0.8 | 1 — silent retry | **no** |
| `payment_timed_out` | notification-gap | 1.0 | 2 | yes |
| `authentication_failed` | notification-gap | 0.8 | 2 | yes |

The two that send nothing are not a bug. Nobody can tell you when a bank will come back up, so
there is nothing to ask a customer about — messaging them would be noise about a problem that is
not theirs.

**Contact hours are real.** Outside 08:00–19:00 IST no message is delivered and the run says so.
To exercise the conversation outside that window, use the clock override, which announces itself:

```bash
uv run python scripts/session.py --scenario insufficient_fund --at '2026-09-06 10:00'
```

### The rules refusing things

```bash
uv run python scripts/inject.py card_declined --at '2026-09-05 18:30'
```

18:30 IST is inside an NPCI restricted band. Expect `WINDOW_VIOLATION` and the retry moved. Cards
are exempt from the UPI Autopay bands and the audit line says so rather than silently passing.

```bash
uv run unhalted run-due --at '2026-09-06 02:00'
```

A nudge due at 02:00 is not sent. Expect `not sent: outside contact hours 08:00-19:00 IST, moved to
08:00`.

```bash
uv run unhalted policy
```

Every threshold the system enforces, read from `config/policy.yaml` — NPCI bands, contact hours, the
retry cap, backoff tiers, confidence thresholds, ladder costs, mandate limits. None of these numbers
is hardcoded anywhere else:

```bash
grep -rn "15000\|15_000" src/unhalted/ | grep -v policy.py
```

Returns nothing.

### What it refuses to guess at

```bash
./scripts/demo_inject.sh
```

Three failures carrying an error reason the taxonomy has no rule for. All three are **held for a
human** — not guessed at, not given a default, nothing scheduled.

```bash
uv run unhalted queue
```

```bash
uv run python scripts/propose_taxonomy_rule.py
```

Groups what is unclassified. Plain counting; no model involved.

To see a rule proposed from documentation you supply, write an excerpt to a file and:

```bash
uv run python scripts/propose_taxonomy_rule.py --method netbanking --reason token_not_found --source issuer_bank --step payment_authorization --file <your-excerpt.txt>
```

It quotes the sentence it reasoned from, and **writes nothing**. Same for policy:

```bash
uv run python scripts/propose_policy_change.py --file <a-circular.txt>
```

`propose_policy_change.py` has no filesystem-write call in it at all, and
`tests/test_propose_policy_change_script.py` inspects the script's own source to prove that.

### The numbers

```bash
uv run unhalted report
```

300 cases, both policies over every one. Money sorted by what a retry can reach, then what each
policy did. Regenerate it yourself — it is deterministic and takes under a second:

```bash
uv run python scripts/run_batch.py
```

The full argument behind each number is in [`batch-measurement.md`](batch-measurement.md).

```bash
uv run unhalted compare <CASE-ID>
```

One case under both policies, side by side. Neither column claims a recovery.

```bash
uv run unhalted breakeven
```

```bash
uv run unhalted calibration
```

### Reading a case

```bash
uv run unhalted cases
uv run unhalted case <CASE-ID>
uv run unhalted stops
uv run unhalted capabilities
```

`capabilities` states what this deployment can and cannot do. Read it before concluding something
is missing — several absences are deliberate and named.

---

## Resetting the database

The default database is `unhalted.db` in the repository root, created on first use. There is no
migration step and nothing to seed.

```bash
rm -f unhalted.db unhalted.db-wal unhalted.db-shm
```

Delete all three. In WAL mode SQLite keeps `-wal` and `-shm` beside the database, and removing only
the main file leaves them behind — the next open then fails with a bare `disk I/O error`. The store
detects that case and names the orphans rather than failing obscurely, but deleting all three avoids
it entirely.

To keep a scratch database instead of touching the default one, set `UNHALTED_DB` **for the whole
command**:

```bash
UNHALTED_DB=/tmp/scratch.db uv run python scripts/session.py --scenario insufficient_fund
```

Two notes. Re-running the same scenario into the same database is treated as a **redelivery** —
payment ids are deterministic, so the case is recognised and nothing new is scheduled. That is
correct behaviour; delete the database between runs of the same scenario. And each scenario uses its
own customer reference, so running all five in one database will not trip the contact ceiling.

---

## Level 2 — against the live Razorpay API

Needs a Razorpay account in **test mode**. Nothing here touches live keys; the project treats live
credentials as permanently out of scope.

### Configure

Copy `.env.example` to `.env` and fill in:

| Variable | Where it comes from |
|---|---|
| `RAZORPAY_KEY_ID` | Dashboard → Settings → API Keys → Generate Test Key |
| `RAZORPAY_KEY_SECRET` | shown once, at the same moment |
| `RAZORPAY_WEBHOOK_SECRET` | any random string; must match the dashboard webhook |
| `OPENROUTER_API_KEY` | for reply parsing |
| `UNHALTED_MODEL` | the model id to parse replies with |

In the dashboard, enable **Subscriptions / Recurring Payments** for Cards and eMandate. Without
that the Subscriptions API returns 401 on every call.

### Check the outside world

```bash
python3 scripts/preflight.py
```

**10 checks, 0 failed.** Credentials, key mode, five Razorpay APIs, the model endpoint, per-call
cost reporting, and the webhook secret. It exits non-zero if anything fails.

```bash
uv run pytest -m live
```

**3 passed, 2 skipped** on a brand-new account — the two skips need a failed payment to exist, and
they skip rather than passing vacuously, which is deliberate. Once the account has produced one
(pay a link, or run the demo), it is 4 passed, 1 skipped.

### Receive a real webhook

Razorpay must be able to reach the service, so it needs a public URL:

```bash
ngrok http 8000
```

Register the tunnel in Dashboard → Settings → Webhooks → Add New Webhook:

- **URL** — `https://<your-tunnel>.ngrok-free.dev/webhooks/razorpay`
- **Secret** — the same value as `RAZORPAY_WEBHOOK_SECRET`
- **Events** — `payment.failed`, `payment_link.paid`, `payment.captured`

The ngrok URL changes every time ngrok restarts; update the dashboard when it does.

Then start the service — **in its own terminal, and before you pay anything**:

```bash
uv run uvicorn unhalted.ingest.webhooks:app --port 8000
```

If nothing is listening when Razorpay posts, the delivery fails. Razorpay does retry, so a case can
recover a minute later once the server is up — but the live path is only observable with it running.

### Watch a real payment close a case

```bash
uv run python scripts/session.py --scenario insufficient_fund
```

The nudge carries a real, payable Razorpay link tagged with the case id. Open it, pay with test card
`4111 1111 1111 1111`, any future expiry, any CVV.

The session is polling. It flips to `recovered` on its own when the webhook arrives, cancels the
pending retry, and sends the customer a confirmation. Nothing about that path is simulated.

---

## Where the claims are checked

| Claim | Checked by |
|---|---|
| Every threshold comes from one file | `tests/test_policy.py`, and the `grep` above |
| Stop rules cannot be overridden | `tests/test_stops.py`, `tests/test_stop_is_durable.py` |
| A stop outlives the queue it emptied | `tests/test_stop_is_durable.py` |
| Amount ceilings run in the live path, not only in their own tests | `tests/test_limits.py`, and four tests from the agent's entry point |
| NPCI windows and the retry cap | `tests/test_scheduler.py` |
| Proposals never write | `tests/test_propose_policy_change_script.py` reads the script's own source |
| Redelivery does not duplicate work | `tests/test_redelivery.py` |
| Lease-based claiming under concurrency | `tests/test_store_concurrency.py` |
| The reply parser, on a labelled corpus | [`reply-evaluation.md`](reply-evaluation.md) |
| The fixtures came off Razorpay's infrastructure | [`capturing-fixtures.md`](capturing-fixtures.md) |

## Where it stops

Read [`BREAKAGE.md`](../BREAKAGE.md) — 26 development failures, what caused each and what changed as
a result. Two of them are gates on the architecture diagram that were fiction until they were
found.

`unhalted capabilities` and the README's *"Built, and deliberately not wired in"* section name what
this deployment does not do. The clearest one: a debit needs a live mandate token, which this
account cannot provision, so `unhalted run-due` on a scheduled retry returns `no executor
registered` rather than reporting a success nobody performed.
