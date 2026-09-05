"""An empty account is asked, not guessed at three times.

`core/reply.py` has always argued this: for the largest failure class, whether
a retry works depends on when the customer will have money, and no API
anywhere reports that. The ladder never acted on it — `RECOVERABLE_BALANCE`
entered at `SILENT_RETRY` and spent NPCI's whole allowance guessing at a date
one question would have settled.

These are the tests for asking first: the question, the fallback behind it for
a customer who never answers, and the cap escalating rather than going quiet.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from unhalted.agent import handle_failure, handle_reply
from unhalted.models import CaseState, FailureSignal
from unhalted.policy import POLICY
from unhalted.shell import ladder
from unhalted.shell.notify import NudgeVariant
from unhalted.shell.scheduler import RETRY_CAP, backoff_for
from unhalted.shell.windows import IST
from unhalted.store import Store

NOW = datetime(2026, 9, 3, 11, 0, tzinfo=IST)


def balance_signal(**over) -> FailureSignal:
    fields = {
        "payment_id": "pay_BAL", "customer_ref": "cust_bal", "amount_paise": 49900,
        "occurred_at": NOW, "source": "test", "method": "card",
        "error_reason": "insufficient_funds", "error_source": "customer",
    }
    fields.update(over)
    return FailureSignal(**fields)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "balance.db"))
    yield s
    s.close()


# -- the question -------------------------------------------------------------


def test_a_balance_failure_asks_when_rather_than_retrying_blind(store) -> None:
    case = handle_failure(store, balance_signal(), now=NOW)

    escalation = next(
        r for r in store.timeline(case.id) if r.decision_type == "escalation"
    )
    assert f"rung {ladder.Rung.NUDGE.value}" in escalation.action

    nudge = next(a for a in store.pending_actions(case_id=case.id) if a["kind"] == "nudge")
    assert nudge["variant"] == NudgeVariant.ASK_DATE.value


def test_the_question_carries_a_message_that_actually_asks(store) -> None:
    """The first-touch wording would not do: it tells them the payment failed
    and offers a link, which is not the same as asking when to try again."""
    from unhalted.shell.notify import body_for

    body = body_for(NudgeVariant.ASK_DATE, 499)
    assert "when would be a good time" in body.lower()
    assert "reply with a date" in body.lower()


def test_the_question_still_offers_a_way_to_pay_now(store) -> None:
    """Asking when to retry must not make somebody who has the money today do
    extra work to hand it over. Every nudge carries a payable link whatever
    else it says — and rung 2 is named "message with a pay link", which was a
    contradiction on screen for the one variant that had none.
    """
    from unhalted.shell.notify import body_for

    body = body_for(NudgeVariant.ASK_DATE, 499, pay_link="https://rzp.io/x/demo")
    assert "https://rzp.io/x/demo" in body
    assert "reply with a date" in body.lower(), "and it still asks"


# -- the fallback behind it ---------------------------------------------------


def test_a_fallback_retry_is_armed_for_a_customer_who_never_answers(store) -> None:
    """Asking is better than guessing, but a silent customer must not leave
    the case waiting forever."""
    case = handle_failure(store, balance_signal(), now=NOW)

    retry = next(a for a in store.pending_actions(case_id=case.id) if a["kind"] == "retry")
    klass = store.latest_diagnosis(case.id).klass
    expected = NOW + POLICY.reply_grace + backoff_for(klass, 0)
    assert datetime.fromisoformat(retry["scheduled_for"]) == expected


def test_the_fallback_waits_for_the_reply_before_the_backoff_starts(store) -> None:
    """It is grace *plus* backoff, not either alone: the customer gets the
    window to answer, and the balance still needs a day to arrive after it."""
    case = handle_failure(store, balance_signal(), now=NOW)
    retry = next(a for a in store.pending_actions(case_id=case.id) if a["kind"] == "retry")
    at = datetime.fromisoformat(retry["scheduled_for"])

    assert at > NOW + POLICY.reply_grace
    assert at > NOW + timedelta(days=1)


def test_an_amount_over_the_ceiling_arms_no_fallback_at_all(store) -> None:
    """The ceiling refuses the fallback for the same reason it refuses any
    other retry — the amount did not become permissible by being asked about."""
    case = handle_failure(store, balance_signal(amount_paise=20_000 * 100), now=NOW)

    assert not any(a["kind"] == "retry" for a in store.pending_actions(case_id=case.id))
    refusal = next(
        r for r in store.timeline(case.id)
        if r.decision_type == "schedule" and "refused on amount" in r.action
    )
    assert refusal.rules_fired == ["LIMIT:WOULD_FAIL"]


# -- what a reply does to it --------------------------------------------------


def test_a_named_date_replaces_the_fallback_rather_than_racing_it(store, monkeypatch) -> None:
    """The whole point of asking: their date wins, and the guess behind it
    is cancelled rather than left armed to fire alongside."""
    from unhalted import agent as agent_module
    from unhalted.models import DetectedIntent, Intent, ParsedReply, Sentiment

    case = handle_failure(store, balance_signal(), now=NOW)
    promised = (NOW + timedelta(days=5)).date()

    monkeypatch.setattr(
        agent_module, "parse_reply",
        lambda text, context="": ParsedReply(
            raw=text,
            intents=[DetectedIntent(type=Intent.PROMISE_TO_PAY, confidence=0.95, evidence=text)],
            payment_date_raw=promised.isoformat(),
            sentiment=Sentiment.NEUTRAL,
        ),
    )

    handle_reply(store, case, "I'll pay on the 8th", now=NOW)

    pending = store.pending_actions(case_id=case.id)
    retries = [a for a in pending if a["kind"] == "retry"]
    assert len(retries) == 1, "the fallback must be replaced, not joined"
    assert datetime.fromisoformat(retries[0]["scheduled_for"]).date() == promised


def test_a_named_date_silences_nudges_until_it_arrives(store, monkeypatch) -> None:
    """`suspend_nudges_until` was computed by the shell and read by nothing,
    so a customer who said "the 8th" could still be nudged on the 7th."""
    from unhalted import agent as agent_module
    from unhalted.models import DetectedIntent, Intent, ParsedReply, Sentiment

    case = handle_failure(store, balance_signal(), now=NOW)
    promised = (NOW + timedelta(days=5)).date()

    monkeypatch.setattr(
        agent_module, "parse_reply",
        lambda text, context="": ParsedReply(
            raw=text,
            intents=[DetectedIntent(type=Intent.PROMISE_TO_PAY, confidence=0.95, evidence=text)],
            payment_date_raw=promised.isoformat(),
            sentiment=Sentiment.NEUTRAL,
        ),
    )
    handle_reply(store, case, "I'll pay on the 8th", now=NOW)

    assert store.get_case(case.id).nudges_suspended_until == promised


def test_a_nudge_does_not_fire_before_the_date_the_customer_named(store) -> None:
    from unhalted.runner import execute_nudge

    case = handle_failure(store, balance_signal(), now=NOW)
    promised = (NOW + timedelta(days=5)).date()
    store.suspend_nudges_until(case.id, promised)

    action = next(a for a in store.pending_actions(case_id=case.id) if a["kind"] == "nudge")
    outcome = execute_nudge(store, action, NOW)

    assert outcome.state == "pending"
    assert promised.isoformat() in outcome.detail
    assert outcome.retry_at is not None and outcome.retry_at.date() == promised


# -- the cap, and what happens after it ---------------------------------------


def test_an_executed_retry_counts_against_the_cap(store) -> None:
    """It was written once, at zero, and never touched again — so every case
    sat permanently on tier one with a cap it could never reach."""
    from unhalted.runner import run_due

    case = handle_failure(store, balance_signal(), now=NOW)
    assert store.get_case(case.id).retry_count == 0

    due = datetime.fromisoformat(
        next(a for a in store.pending_actions(case_id=case.id) if a["kind"] == "retry")[
            "scheduled_for"
        ]
    )
    run_due(store, now=due)

    assert store.get_case(case.id).retry_count == 1


def test_a_deferred_nudge_is_not_counted_as_a_debit_attempt(store) -> None:
    """A message moved out of contact hours is not an attempt on the mandate."""
    from unhalted.runner import run_due

    case = handle_failure(store, balance_signal(), now=NOW)
    midnight = NOW.replace(hour=2, minute=0)
    run_due(store, now=midnight)

    assert store.get_case(case.id).retry_count == 0


def test_the_cap_escalates_to_a_payable_link_instead_of_going_quiet(store) -> None:
    """The four paths that ask for a retry could all be refused by the cap,
    and each one used to write "refused" and stop — a case with nothing
    pending and nobody told."""
    case = handle_failure(store, balance_signal(), now=NOW)
    store.cancel_pending("TEST_SETUP", case_id=case.id)
    for _ in range(RETRY_CAP):
        store.increment_retry_count(case.id)

    handle_failure(
        store, balance_signal(payment_id="pay_BAL2"), now=NOW,
    )  # a second, separate case is unaffected

    from unhalted.agent import resume_after_review

    resume_after_review(store, store.get_case(case.id), now=NOW)

    pending = store.pending_actions(case_id=case.id)
    assert [a["kind"] for a in pending] == ["nudge"]
    assert pending[0]["variant"] == NudgeVariant.EXHAUSTED.value


def test_the_exhausted_message_says_why_it_is_arriving(store) -> None:
    """Reusing the first-touch wording on somebody who just asked for a
    specific date reads as never having listened."""
    from unhalted.shell.notify import body_for

    body = body_for(NudgeVariant.EXHAUSTED, 499, pay_link="https://rzp.io/x")
    assert "couldn't collect" in body
    assert "https://rzp.io/x" in body


def test_a_second_exhaustion_holds_for_a_person_rather_than_nudging_again(store) -> None:
    """Once the link has gone out, the ladder really is finished."""
    from unhalted.agent import resume_after_review

    case = handle_failure(store, balance_signal(), now=NOW)
    store.cancel_pending("TEST_SETUP", case_id=case.id)
    for _ in range(RETRY_CAP):
        store.increment_retry_count(case.id)

    resume_after_review(store, store.get_case(case.id), now=NOW)
    store.cancel_pending("TEST_SETUP", case_id=case.id)
    resume_after_review(store, store.get_case(case.id), now=NOW)

    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN
    assert store.pending_actions(case_id=case.id) == []
    end = next(r for r in store.timeline(case.id) if "LADDER_END" in r.rules_fired)
    assert "already sent" in end.outcome
