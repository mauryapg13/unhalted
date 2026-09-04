# Batch measurement

Generated 2026-09-04 16:58 UTC · 300 cases ·
Rs 120,850 at risk

## What this batch is

The failures are generated. Their error reasons, sources and steps come from Razorpay's published
taxonomy — the same data the diagnosis runs on, pinned to a commit of their documentation — but
the volume and the frequency mix are synthetic, and the mix in particular is a hand-weighted
judgement nobody measured.

A test account produces one real failure per human click, and only ever a generic
`payment_failed`; both checkout surfaces were tested. So a batch large enough to compare two
policies has to be built, and this says which parts are real.

The control is **Razorpay's own documented behaviour**: three automatic retries on consecutive
days, no diagnosis, no contact, then halted. Not a strawman — it is what their subscription
documentation says happens today.

**On the holdout.** 21 of 300 cases were assigned to it, and it does no work here.
A control group absorbs unobserved variation, and a generated batch running two deterministic
policies has none. Both policies were run over *every* case instead, which is a paired comparison
and strictly stronger. The holdout stays for the day this runs against real traffic.

## Part two — modelled, and not a measurement

Shown first because it is the number everyone asks for. The counted facts it is built from —
nothing here needs to know whether anything recovered — follow immediately after, in Part one.

Rupees recovered depends on how often a recovery attempt works, and this project cannot measure
that: it needs real outcomes at volume. So it is shown as a range. The rates are inputs, not
findings.

| If attempts succeed at | Agent | Baseline | Difference |
|---|---:|---:|---:|
| 20% | Rs 24,170 | Rs 20,947 | Rs 3,223 |
| 30% | Rs 36,255 | Rs 31,421 | Rs 4,834 |
| 40% | Rs 48,340 | Rs 41,895 | Rs 6,445 |
| 50% | Rs 60,425 | Rs 52,368 | Rs 8,057 |

The agent's advantage in this model comes from one thing only: it does not spend attempts on
failures that a retry provably cannot fix. Everything else in the difference would require knowing
how customers respond, which nobody here does.

**No line in this section is a measurement, and none should be quoted as one.**

## Part one — counted, no assumptions

Facts about what each policy does. Nothing here needs to know whether anything recovered.

| | Agent | Baseline | |
|---|---|---|---|
| Debit attempts scheduled | 217 | 900 | 683 fewer |
| Attempts a retry could not fix | 0 | 108 | 108 avoided |
| Attempts inside NPCI restricted bands | 0 | 117 | 117 avoided |
| Customer contacts | 56 | 0 | baseline never contacts anyone |
| Cases held for a human | 27 | 0 | baseline has no such path |
| Cases closed as uneconomic | 0 | 0 | unreachable at entry rungs; see below |

Intervention spend: **Rs 92**.

### What the model was asked to do

| | Count | Share |
|---|---:|---:|
| Diagnoses resolved from the rules table | 300 | 100% |
| Diagnoses that required a model call | 0 | 0% |

Inference spend: **Rs 0.00** across 0 call(s).

**Zero is the measurement, not a missing one.** Every failure in this batch was drawn from Razorpay's documented taxonomy, and every one of them resolved deterministically. Inference cost nothing because nothing needed inferring — which is the 85% claim in this README appearing as a count.

This figure covers **diagnosis only**, because that is all this batch contains. The model's other
work — parsing customer replies, drafting messages, briefing a human — needs a customer on the
other end, and a generated batch has nobody to reply. Measured separately, reply parsing costs
about Rs 0.01 per message against OpenRouter's reported `usage.cost`; see
`docs/reply-evaluation.md`. The model is not free. It was not needed here.

### Why no case was closed as uneconomic

The count above is **0**, and that is arithmetic rather than a gap in the
gate. The provable half refuses a rung costing more than the whole amount at stake. Cases enter
the ladder by diagnosis class, and the most expensive entry rung is re-authorisation at
Rs 2; the smallest amount in this batch is Rs 49.
No entry rung can cost more than the stake, so the provable gate is unreachable at entry — by
inspection, at any batch size.

It becomes reachable on **escalation**, where a Rs 60 human callback meets a Rs 49 subscription.
This batch does not escalate, and cannot: escalating means deciding that the previous rung failed,
which is an outcome model, and the reason this report has a part two is that this project refuses
to write one. So the gate is exercised by `tests/test_ladder.py` against stated amounts, not by
this batch. Recorded rather than papered over: see issue #15.

### Where the cases went

| Diagnosis | Cases | Share |
|---|---:|---:|
| `recoverable-balance` | 133 | 44% |
| `recoverable-technical` | 107 | 36% |
| `mandate-state-broken` | 36 | 12% |
| `notification-gap` | 20 | 7% |
| `unknown` | 4 | 1% |

| Entry rung | Cases |
|---|---:|
| rung 1 | 217 |
| rung 2 | 20 |
| rung 3 | 36 |

### How much the confidence thresholds matter

Issue #7 asks whether `0.90` and `0.70` are the right cut-points. That cannot be settled here —
it needs ground truth for classification correctness, and on generated data the correct class is
whatever was generated. What *is* countable is how much the choice matters.

| Band | Cases | Share |
|---|---:|---:|
| `0.70-0.89 sampled` | 89 | 30% |
| `<0.70 held` | 27 | 9% |
| `>=0.90 auto` | 184 | 61% |
