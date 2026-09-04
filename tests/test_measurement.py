"""The batch, the baseline, and the claims the report is allowed to make.

The measurement is the most dangerous part of this project: it is where a
number that nobody can defend would do the most damage. These tests are mostly
about what the report must *not* assert.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from unhalted.measure import baseline
from unhalted.measure.generate import REASON_WEIGHTS, generate
from unhalted.measure.report import band, render, render_terminal, run_batch
from unhalted.models import DiagnosisClass, FailureSignal
from unhalted.shell.windows import IST
from unhalted.store import Store

# -- the batch ---------------------------------------------------------------


def test_the_batch_is_reproducible() -> None:
    """A reported number has to be regenerable exactly."""
    a = generate(120, seed=7)
    b = generate(120, seed=7)
    assert [c.signal.payment_id for c in a] == [c.signal.payment_id for c in b]
    assert [c.signal.error_reason for c in a] == [c.signal.error_reason for c in b]
    assert [c.holdout for c in a] == [c.holdout for c in b]


def test_a_different_seed_gives_a_different_batch() -> None:
    assert [c.signal.error_reason for c in generate(120, seed=1)] != [
        c.signal.error_reason for c in generate(120, seed=2)
    ]


def test_every_generated_reason_exists_in_razorpay_s_taxonomy() -> None:
    """The mix is invented; the vocabulary is not."""
    import json

    from unhalted.core.taxonomy import DATA_FILE

    data = json.loads(DATA_FILE.read_text())
    known = set(data["reasons"]["any"]) | set(data["reasons"]["card"]) | set(
        data["reasons"]["upi"]
    )
    assert set(REASON_WEIGHTS) <= known


def test_generated_signals_are_marked_as_generated() -> None:
    """Nothing should be able to mistake these for captured payments."""
    for case in generate(20):
        assert case.signal.source == "generated:razorpay-taxonomy"


def test_cohorts_are_assigned_before_any_policy_runs() -> None:
    cases = generate(400, holdout_pct=10)
    held = sum(1 for c in cases if c.holdout)
    assert 0 < held < len(cases)
    assert abs(held / len(cases) - 0.10) < 0.05


# -- the baseline is Razorpay's, not a strawman ------------------------------


def signal(hour: int = 11) -> FailureSignal:
    return FailureSignal(
        payment_id="pay_B", customer_ref="c", amount_paise=49900,
        occurred_at=datetime(2026, 9, 1, hour, 30, tzinfo=IST), source="test",
    )


def test_the_baseline_retries_exactly_what_razorpay_documents() -> None:
    """T+1, T+2, T+3, then halted."""
    run = baseline.run(signal(), DiagnosisClass.RECOVERABLE_BALANCE)
    assert run.attempts == baseline.BASELINE_RETRIES == 3


def test_the_baseline_never_contacts_anybody() -> None:
    run = baseline.run(signal(), DiagnosisClass.RECOVERABLE_BALANCE)
    assert run.messages_sent == 0
    assert run.intervention_paise == 0


def test_the_baseline_spends_every_attempt_on_a_failure_it_cannot_fix() -> None:
    """It has no diagnosis, so it cannot know the card is expired."""
    run = baseline.run(signal(), DiagnosisClass.MANDATE_STATE_BROKEN)
    assert run.futile_attempts == 3


def test_the_baseline_retries_inside_forbidden_hours_on_upi() -> None:
    """Not a strawman: retrying at the same time of day lands in a restricted
    band whenever the original failure did."""
    upi = signal(hour=11).model_copy(update={"method": "upi"})
    assert baseline.run(upi, DiagnosisClass.RECOVERABLE_BALANCE
                        ).attempts_in_restricted_window == 3
    allowed = signal(hour=14).model_copy(update={"method": "upi"})
    assert baseline.run(allowed, DiagnosisClass.RECOVERABLE_BALANCE
                        ).attempts_in_restricted_window == 0


@pytest.mark.parametrize("method", ["card", "emandate", "netbanking", None])
def test_only_upi_is_governed_by_the_npci_execution_bands(method) -> None:
    """The bands are a UPI Autopay rule. Counting a card retry against them
    credited this system with an advantage it does not have — 705 violations
    became 213 on a mandate-heavy mix once the rule was scoped to its rail."""
    at_eleven = signal(hour=11).model_copy(update={"method": method})
    run = baseline.run(at_eleven, DiagnosisClass.RECOVERABLE_BALANCE)
    assert run.attempts == 3, "the retry count is unchanged"
    assert run.attempts_in_restricted_window == 0


def test_a_retry_count_razorpay_does_not_document_is_declared_as_assumed() -> None:
    """Their emandate tab states no count and no daily cadence."""
    upi = signal().model_copy(update={"method": "upi"})
    assert not baseline.run(upi, DiagnosisClass.RECOVERABLE_BALANCE).assumption_used

    emandate = signal().model_copy(update={"method": "emandate"})
    run = baseline.run(emandate, DiagnosisClass.RECOVERABLE_BALANCE)
    assert run.assumption_used
    assert any("no retry count" in n for n in run.notes)


def test_emandate_attempts_are_never_closer_than_a_day_apart() -> None:
    """Razorpay: the next attempt waits on the previous one settling, which
    may exceed 24 hours. Modelled at exactly 24 — the fastest they allow, and
    so the reading that cannot inflate the agent's advantage."""
    from datetime import datetime as _dt
    from itertools import pairwise

    emandate = signal().model_copy(update={"method": "emandate"})
    stamps = [_dt.fromisoformat(t) for t in
              baseline.run(emandate, DiagnosisClass.RECOVERABLE_BALANCE).scheduled_at]
    gaps = [(b - a).total_seconds() / 3600 for a, b in pairwise(stamps)]
    assert all(g >= 24 for g in gaps)


# -- what the comparison may claim -------------------------------------------


@pytest.fixture
def batch(tmp_path):
    cases = generate(60, seed=99)
    store = Store(str(tmp_path / "b.db"))
    try:
        agent, base = run_batch(cases, store)
    finally:
        store.close()
    return cases, agent, base


def test_the_agent_schedules_fewer_attempts_than_the_baseline(batch) -> None:
    _, agent, base = batch
    assert agent.attempts < base.attempts


def test_the_agent_spends_no_attempts_on_failures_a_retry_cannot_fix(batch) -> None:
    _, agent, base = batch
    assert agent.futile_attempts == 0
    assert base.futile_attempts > 0


def test_the_agent_never_schedules_inside_a_restricted_band(batch) -> None:
    _, agent, base = batch
    assert agent.attempts_in_restricted_window == 0
    assert base.attempts_in_restricted_window > 0


def test_every_case_lands_in_exactly_one_confidence_band(batch) -> None:
    _, agent, _ = batch
    assert sum(agent.by_confidence_band.values()) == agent.cases


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(1.0, ">=0.90 auto"), (0.9, ">=0.90 auto"), (0.89, "0.70-0.89 sampled"),
     (0.7, "0.70-0.89 sampled"), (0.69, "<0.70 held"), (0.0, "<0.70 held")],
)
def test_confidence_bands_match_the_authority_thresholds(confidence, expected) -> None:
    assert band(confidence) == expected


# -- the report's honesty ----------------------------------------------------


def test_the_report_says_the_batch_is_generated(batch) -> None:
    cases, agent, base = batch
    text = render(cases, agent, base)
    assert "The failures are generated" in text
    assert "synthetic" in text


def test_the_report_names_the_baseline_as_razorpay_s_own(batch) -> None:
    """A control the judge can verify beats one we designed."""
    cases, agent, base = batch
    text = render(cases, agent, base)
    assert "documented behaviour" in text
    assert "Not a strawman" in text


def test_the_report_refuses_to_call_the_rupee_figure_a_measurement(batch) -> None:
    cases, agent, base = batch
    text = render(cases, agent, base)
    assert "No line in this section is a measurement" in text
    assert "modelled" in text.lower()


def test_the_report_admits_the_holdout_does_nothing_here(batch) -> None:
    """A holdout absorbs unobserved variation, and generated data has none."""
    cases, agent, base = batch
    text = render(cases, agent, base)
    assert "it does no work here" in text


def test_the_report_separates_counted_from_modelled(batch) -> None:
    """Modelled leads, since it's the number everyone asks for first — but the
    two must never interleave, and the modelled section must say plainly that
    the counted facts justifying it come right after."""
    cases, agent, base = batch
    text = render(cases, agent, base)
    assert text.index("Part two — modelled") < text.index("Part one — counted")
    assert "follow immediately after, in Part one" in text


def test_every_diagnosis_is_attributed_to_a_source(batch) -> None:
    """Issue #16: how the diagnosis was reached is counted, not assumed."""
    _, agent, _ = batch
    assert sum(agent.by_source.values()) == agent.cases


def test_the_report_counts_model_calls_and_not_only_spend(batch) -> None:
    """A bare `Rs 0.00` reads as unmeasured. A count of calls reads as measured."""
    cases, agent, base = batch
    text = render(cases, agent, base)
    assert "Diagnoses that required a model call" in text
    assert "Zero is the measurement, not a missing one" in text


def test_the_report_says_the_spend_figure_covers_diagnosis_only(batch) -> None:
    """A reader could reasonably assume it covers reply parsing too. It does not."""
    cases, agent, base = batch
    text = render(cases, agent, base)
    assert "diagnosis only" in text


# -- the terminal render, a screen away from the doc --------------------------


def test_the_terminal_render_carries_no_markdown_syntax(batch) -> None:
    """`unhalted report` used to print the doc's raw markdown — every `|` and
    every `**bold**` landed on a terminal literally. This is what replaced it."""
    cases, agent, base = batch
    total_paise = sum(c.signal.amount_paise for c in cases)
    text = render_terminal(
        agent, base, cases_count=agent.cases,
        holdout=sum(1 for c in cases if c.holdout),
        total_paise=total_paise, generated_at="2026-09-04 16:58 UTC",
    )
    assert "|" not in text
    assert "**" not in text
    assert "##" not in text


def test_the_terminal_render_carries_the_same_counted_numbers(batch) -> None:
    """A screen summary is only useful if it agrees with the document it
    summarises — the two must never quietly drift apart."""
    cases, agent, base = batch
    total_paise = sum(c.signal.amount_paise for c in cases)
    text = render_terminal(
        agent, base, cases_count=agent.cases,
        holdout=sum(1 for c in cases if c.holdout),
        total_paise=total_paise, generated_at="2026-09-04 16:58 UTC",
    )
    assert str(agent.attempts) in text
    assert str(base.attempts) in text
    assert str(base.attempts - agent.attempts) in text
    assert "full report: docs/batch-measurement.md" in text


def test_the_report_explains_why_nothing_was_closed_as_uneconomic(batch) -> None:
    """Issue #15: a zero here is arithmetic, and the report has to say which."""
    cases, agent, base = batch
    assert agent.closed_uneconomic == 0
    text = render(cases, agent, base)
    assert "Why no case was closed as uneconomic" in text
    assert "unreachable at entry" in text
