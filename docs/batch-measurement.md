# Batch measurement

Generated 2026-09-02 12:39 UTC · 300 cases ·
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

## Part one — counted, no assumptions

Facts about what each policy does. Nothing here needs to know whether anything recovered.

| | Agent | Baseline | |
|---|---|---|---|
| Debit attempts scheduled | 217 | 900 | 683 fewer |
| Attempts a retry could not fix | 0 | 108 | 108 avoided |
| Attempts inside NPCI restricted bands | 0 | 315 | 315 avoided |
| Customer contacts | 56 | 0 | baseline never contacts anyone |
| Cases held for a human | 27 | 0 | baseline has no such path |
| Cases closed as uneconomic | 0 | 0 | with the arithmetic recorded |

Intervention spend: **Rs 92**.
Inference spend: **Rs 0.00**.

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
| `0.70-0.89 sampled` | 68 | 23% |
| `<0.70 held` | 27 | 9% |
| `>=0.90 auto` | 205 | 68% |

## Part two — modelled, and not a measurement

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
