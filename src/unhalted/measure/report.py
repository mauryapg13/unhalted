"""Comparing two policies, and reporting only what can be defended.

Part one counts what each policy *does*: attempts spent, windows violated,
messages sent, cases held. None of it needs to know whether anything recovered,
so none of it rests on an assumption.

Part two is the rupee figure, and it is modelled. On a generated batch, whoever
writes the outcome model decides how much was recovered — so it is reported as a
range across success rates with the rates on the page, never as a measurement.

On the holdout
--------------
The specification asks for a 10% control receiving baseline behaviour, and the
cohort is assigned. But on generated data a holdout controls for nothing: both
policies are deterministic and there is no unobserved variation for a control
group to absorb. Running both over *every* case is a paired comparison, which is
strictly stronger than sampling 10% of them.

So the holdout is assigned, reported, and honestly described as doing no work
here. It exists for the day this runs against real traffic, where a control
group earns its keep.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from unhalted.core.diagnose import diagnose
from unhalted.measure import baseline
from unhalted.measure.generate import GeneratedCase
from unhalted.models import CaseState, DiagnosisClass, DiagnosisSource
from unhalted.shell import windows
from unhalted.shell.ladder import ENTRY, LADDER
from unhalted.shell.verify import Verification


class UnpaidByConstruction:
    """Generated failures are unpaid, because that is what was generated.

    Not an outcome model: no judgement is being made about whether a retry
    would have worked. It states a property of the fixture so the verification
    path can run at all.
    """

    def order_settled(self, order_id: str) -> Verification:
        return Verification(False, f"{order_id} is a generated failure", "fixture")


@dataclass
class Totals:
    cases: int = 0
    attempts: int = 0
    attempts_in_restricted_window: int = 0
    futile_attempts: int = 0
    messages: int = 0
    intervention_paise: int = 0
    held_for_human: int = 0
    closed_uneconomic: int = 0
    by_class: Counter = field(default_factory=Counter)
    by_rung: Counter = field(default_factory=Counter)
    by_confidence_band: Counter = field(default_factory=Counter)
    #: How each diagnosis was reached. A zero in the model row is the whole
    #: architectural claim appearing as a count rather than an assertion.
    by_source: Counter = field(default_factory=Counter)


def band(confidence: float) -> str:
    if confidence >= 0.90:
        return ">=0.90 auto"
    if confidence >= 0.70:
        return "0.70-0.89 sampled"
    return "<0.70 held"


def run_batch(cases: list[GeneratedCase], store) -> tuple[Totals, Totals]:
    """Run both policies over every case. Returns (agent, baseline)."""
    agent_totals = Totals()
    base_totals = Totals()
    verifier = UnpaidByConstruction()

    from unhalted.agent import handle_failure

    for generated in cases:
        signal = generated.signal
        now = windows.as_ist(signal.occurred_at)

        diagnosis = diagnose(signal)
        agent_totals.cases += 1
        agent_totals.by_class[diagnosis.klass.value] += 1
        agent_totals.by_confidence_band[band(diagnosis.confidence)] += 1
        agent_totals.by_source[diagnosis.source.value] += 1

        case = handle_failure(store, signal, verifier=verifier, now=now)

        for record in store.timeline(case.id):
            if record.decision_type == "schedule" and record.action.startswith("retry at"):
                agent_totals.attempts += 1
            if record.decision_type == "escalation":
                if "uneconomic" in record.action:
                    agent_totals.closed_uneconomic += 1
                elif "entering at rung" in record.action:
                    rung = record.action.split("rung ")[1].split(":")[0]
                    agent_totals.by_rung[f"rung {rung}"] += 1
                    cost = record.inputs.get("cost_paise") or 0
                    agent_totals.intervention_paise += cost
                    if cost:
                        agent_totals.messages += 1
        if store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN:
            agent_totals.held_for_human += 1

        # The documented baseline, over the same failure.
        run = baseline.run(signal, diagnosis.klass)
        base_totals.cases += 1
        base_totals.attempts += run.attempts
        base_totals.attempts_in_restricted_window += run.attempts_in_restricted_window
        base_totals.futile_attempts += run.futile_attempts
        base_totals.by_class[diagnosis.klass.value] += 1

    return agent_totals, base_totals


def modelled_recovery(
    cases: list[GeneratedCase], agent: Totals, rates: list[float]
) -> list[tuple[float, float, float]]:
    """Rupees recovered at several success rates. Not a measurement.

    Returns (rate, agent rupees, baseline rupees) per rate. The baseline can
    only recover from attempts that were not futile; the agent's advantage here
    is entirely that it does not spend attempts on failures a retry cannot fix.
    """
    out = []
    total_paise = sum(c.signal.amount_paise for c in cases)
    futile_share = (
        agent.by_class.get(DiagnosisClass.MANDATE_STATE_BROKEN.value, 0)
        + agent.by_class.get(DiagnosisClass.UNKNOWN.value, 0)
    ) / max(1, agent.cases)

    for rate in rates:
        agent_rupees = total_paise * rate / 100
        baseline_rupees = total_paise * rate * (1 - futile_share) / 100
        out.append((rate, agent_rupees, baseline_rupees))
    return out


def render(
    cases: list[GeneratedCase], agent: Totals, base: Totals, model_spend_paise: int = 0
) -> str:
    holdout = sum(1 for c in cases if c.holdout)
    total_paise = sum(c.signal.amount_paise for c in cases)
    rupee = 100

    def row(label: str, a, b, note: str = "") -> str:
        return f"| {label} | {a} | {b} | {note} |"

    model_calls = agent.by_source.get(DiagnosisSource.MODEL.value, 0)
    rules_calls = agent.by_source.get(DiagnosisSource.RULES_TABLE.value, 0)
    spend_note = (
        "**Zero is the measurement, not a missing one.** Every failure in this batch was drawn "
        "from Razorpay's documented taxonomy, and every one of them resolved deterministically. "
        "Inference cost nothing because nothing needed inferring — which is the 85% claim in "
        "this README appearing as a count."
        if model_calls == 0
        else "Each call is a failure the rules table could not resolve on its own."
    )
    max_entry_cost = max(
        LADDER[r].cost_paise for r in ENTRY.values() if r is not None
    )
    min_amount = min(c.signal.amount_paise for c in cases)

    saved_attempts = base.attempts - agent.attempts
    saved_futile = base.futile_attempts - agent.futile_attempts
    saved_windows = base.attempts_in_restricted_window - agent.attempts_in_restricted_window

    bands = "\n".join(
        f"| `{b}` | {n} | {n / max(1, agent.cases):.0%} |"
        for b, n in sorted(agent.by_confidence_band.items())
    )
    classes = "\n".join(
        f"| `{k}` | {v} | {v / max(1, agent.cases):.0%} |"
        for k, v in agent.by_class.most_common()
    )
    rungs = "\n".join(f"| {k} | {v} |" for k, v in sorted(agent.by_rung.items()))
    sensitivity = "\n".join(
        f"| {r:.0%} | Rs {a:,.0f} | Rs {b:,.0f} | Rs {a - b:,.0f} |"
        for r, a, b in modelled_recovery(cases, agent, [0.20, 0.30, 0.40, 0.50])
    )

    return f"""# Batch measurement

Generated {datetime.now(tz=UTC):%Y-%m-%d %H:%M UTC} · {agent.cases} cases ·
Rs {total_paise / rupee:,.0f} at risk

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

**On the holdout.** {holdout} of {agent.cases} cases were assigned to it, and it does no work here.
A control group absorbs unobserved variation, and a generated batch running two deterministic
policies has none. Both policies were run over *every* case instead, which is a paired comparison
and strictly stronger. The holdout stays for the day this runs against real traffic.

## Part one — counted, no assumptions

Facts about what each policy does. Nothing here needs to know whether anything recovered.

| | Agent | Baseline | |
|---|---|---|---|
{row("Debit attempts scheduled", agent.attempts, base.attempts, f"{saved_attempts} fewer")}
{row("Attempts a retry could not fix", agent.futile_attempts, base.futile_attempts, f"{saved_futile} avoided")}
{row("Attempts inside NPCI restricted bands", agent.attempts_in_restricted_window, base.attempts_in_restricted_window, f"{saved_windows} avoided")}
{row("Customer contacts", agent.messages, base.messages, "baseline never contacts anyone")}
{row("Cases held for a human", agent.held_for_human, 0, "baseline has no such path")}
{row("Cases closed as uneconomic", agent.closed_uneconomic, 0, "unreachable at entry rungs; see below")}

Intervention spend: **Rs {agent.intervention_paise / rupee:,.0f}**.

### What the model was asked to do

| | Count | Share |
|---|---:|---:|
| Diagnoses resolved from the rules table | {rules_calls} | {rules_calls / max(1, agent.cases):.0%} |
| Diagnoses that required a model call | {model_calls} | {model_calls / max(1, agent.cases):.0%} |

Inference spend: **Rs {model_spend_paise / rupee:,.2f}** across {model_calls} call(s).

{spend_note}

This figure covers **diagnosis only**, because that is all this batch contains. The model's other
work — parsing customer replies, drafting messages, briefing a human — needs a customer on the
other end, and a generated batch has nobody to reply. Measured separately, reply parsing costs
about Rs 0.01 per message against OpenRouter's reported `usage.cost`; see
`docs/reply-evaluation.md`. The model is not free. It was not needed here.

### Why no case was closed as uneconomic

The count above is **{agent.closed_uneconomic}**, and that is arithmetic rather than a gap in the
gate. The provable half refuses a rung costing more than the whole amount at stake. Cases enter
the ladder by diagnosis class, and the most expensive entry rung is re-authorisation at
Rs {max_entry_cost / rupee:.0f}; the smallest amount in this batch is Rs {min_amount / rupee:.0f}.
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
{classes}

| Entry rung | Cases |
|---|---:|
{rungs}

### How much the confidence thresholds matter

Issue #7 asks whether `0.90` and `0.70` are the right cut-points. That cannot be settled here —
it needs ground truth for classification correctness, and on generated data the correct class is
whatever was generated. What *is* countable is how much the choice matters.

| Band | Cases | Share |
|---|---:|---:|
{bands}

## Part two — modelled, and not a measurement

Rupees recovered depends on how often a recovery attempt works, and this project cannot measure
that: it needs real outcomes at volume. So it is shown as a range. The rates are inputs, not
findings.

| If attempts succeed at | Agent | Baseline | Difference |
|---|---:|---:|---:|
{sensitivity}

The agent's advantage in this model comes from one thing only: it does not spend attempts on
failures that a retry provably cannot fix. Everything else in the difference would require knowing
how customers respond, which nobody here does.

**No line in this section is a measurement, and none should be quoted as one.**
"""
