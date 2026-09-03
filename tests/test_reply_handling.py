"""What a reply changes, from the agent's entry point.

The consequence of a reply is the agent's business, not the surface that
received it. These call `handle_reply` rather than the parser, because the bug
they exist to prevent was a decision that worked and a consequence that never
fired.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from unhalted import agent
from unhalted.models import (
    CaseState,
    DetectedIntent,
    FailureSignal,
    Intent,
    ParsedReply,
    Sentiment,
)
from unhalted.shell.windows import IST
from unhalted.store import Store

NOW = datetime(2026, 9, 20, 14, 0, tzinfo=IST)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "reply.db"))
    yield s
    s.close()


@pytest.fixture
def case(store):
    signal = FailureSignal(
        payment_id="pay_REPLY1",
        customer_ref="cust_reply",
        amount_paise=49900,
        occurred_at=NOW,
        source="test",
        method="card",
        error_reason="insufficient_funds",
        error_source="customer",
    )
    c = agent.handle_failure(store, signal, now=NOW)
    # handle_failure schedules the retry itself; the nudges are added here so
    # a stop has several things of different kinds to cancel at once.
    store.schedule_action(c.id, c.customer_ref, "nudge", NOW, NOW)
    store.schedule_action(c.id, c.customer_ref, "voice-callback", NOW, NOW)
    return c


def fake_parse(*intents, date_raw=None, sentiment=Sentiment.NEUTRAL, failed=False):
    """Replaces the model, so these test the consequence rather than the reading."""

    def _parse(text, context=""):
        return ParsedReply(
            raw=text,
            intents=[DetectedIntent(type=i, confidence=c, evidence=text) for i, c in intents],
            payment_date_raw=date_raw,
            sentiment=sentiment,
            failed=failed,
            failure_reason="stubbed failure" if failed else None,
        )

    return _parse


def test_a_cancellation_request_cancels_the_pending_retry(store, case, monkeypatch) -> None:
    """The bug this file exists for: the agent must not charge someone who
    just asked to cancel while a person gets round to actioning it."""
    monkeypatch.setattr(agent, "parse_reply", fake_parse((Intent.CANCELLATION_REQUEST, 0.97)))

    before = store.pending_actions(case_id=case.id)
    assert {a["kind"] for a in before} == {"retry", "nudge", "voice-callback"}
    agent.handle_reply(store, case, "cancel it please", now=NOW)

    assert store.pending_actions(case_id=case.id) == []
    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN


def test_an_unreadable_reply_does_not_leave_a_retry_armed(store, case, monkeypatch) -> None:
    monkeypatch.setattr(agent, "parse_reply", fake_parse(failed=True))
    agent.handle_reply(store, case, "???", now=NOW)
    assert store.pending_actions(case_id=case.id) == []
    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN


def test_a_dispute_halts_everything(store, case, monkeypatch) -> None:
    monkeypatch.setattr(agent, "parse_reply", fake_parse((Intent.DISPUTE, 0.9)))
    agent.handle_reply(store, case, "double debit hua tha", now=NOW)
    assert store.pending_actions(customer_ref=case.customer_ref) == []
    assert store.get_case(case.id).state is CaseState.HELD_FOR_HUMAN


def test_a_promise_replaces_the_pending_retry_rather_than_adding_one(
    store, case, monkeypatch
) -> None:
    monkeypatch.setattr(
        agent, "parse_reply",
        fake_parse((Intent.PROMISE_TO_PAY, 0.95), date_raw="2026-09-25"),
    )
    agent.handle_reply(store, case, "25 tarikh ko kar dunga", now=NOW)

    pending = store.pending_actions(case_id=case.id)
    assert len(pending) == 1, "the old retry should be replaced, not duplicated"
    assert pending[0]["kind"] == "retry"
    assert pending[0]["scheduled_for"].startswith("2026-09-25")


def test_a_promise_with_no_usable_date_changes_no_timing(store, case, monkeypatch) -> None:
    """The specification: record the promise, ask them to confirm a date."""
    monkeypatch.setattr(
        agent, "parse_reply",
        fake_parse((Intent.PROMISE_TO_PAY, 0.95), date_raw="2026-02-31"),
    )
    before = store.pending_actions(case_id=case.id)
    _, outcome = agent.handle_reply(store, case, "31 feb ko", now=NOW)

    assert outcome.realign_to is None
    assert "PROMISE_WITHOUT_USABLE_DATE" in outcome.rules_fired
    assert len(store.pending_actions(case_id=case.id)) == len(before), "nothing should have moved"


def test_every_reply_is_written_to_the_audit_trail(store, case, monkeypatch) -> None:
    monkeypatch.setattr(agent, "parse_reply", fake_parse((Intent.OPT_OUT, 0.98)))
    agent.handle_reply(store, case, "stop messaging me", now=NOW)

    replies = [r for r in store.timeline(case.id) if r.decision_type == "reply"]
    assert len(replies) == 1
    assert replies[0].inputs["reply"] == "stop messaging me"
    assert replies[0].inputs["intents"][0]["type"] == "opt-out"
    assert "STOP_RULE:OPT_OUT" in replies[0].rules_fired


def test_the_reply_records_the_evidence_the_model_quoted(store, case, monkeypatch) -> None:
    """An auditor needs to see why, not only what."""
    monkeypatch.setattr(agent, "parse_reply", fake_parse((Intent.DISTRESS, 0.97)))
    agent.handle_reply(store, case, "job chali gayi", now=NOW)

    reply = next(r for r in store.timeline(case.id) if r.decision_type == "reply")
    assert reply.inputs["intents"][0]["evidence"]


def test_a_held_case_reports_the_date_today_correctly(store, case, monkeypatch) -> None:
    """Guards against a promise being validated against the wrong day."""
    monkeypatch.setattr(
        agent, "parse_reply",
        fake_parse((Intent.PROMISE_TO_PAY, 0.95), date_raw=date(2026, 9, 19).isoformat()),
    )
    _, outcome = agent.handle_reply(store, case, "kal", now=NOW)
    assert outcome.realign_to is None, "a date in the past must be refused"


# --- Findings from the exploratory pass -----------------------------------


def test_evidence_that_does_not_quote_the_reply_is_dropped() -> None:
    """Issue #25. An invented span is shown to a reviewer as if it were a quote."""
    from unhalted.core.reply import _quotes_the_reply

    reply = "salary aayega 5th ko, tab try karna"
    assert _quotes_the_reply("salary aayega", reply)
    assert _quotes_the_reply("SALARY   AAYEGA", reply), "spacing and case are not meaning"
    assert not _quotes_the_reply("", reply)
    assert not _quotes_the_reply("   ", reply)
    assert not _quotes_the_reply("I will pay you next month", reply)


def test_a_truncated_response_is_not_retried(monkeypatch) -> None:
    """Issue #22. At temperature 0 it truncates identically and bills each time.

    Needs a key configured, or `_call_model` returns before `httpx.post` is ever
    reached — true here whenever nothing has loaded a real one into the
    environment, CI included, and the mock below then goes uncalled.
    """
    import httpx

    from unhalted.core import reply as reply_mod

    monkeypatch.setattr(reply_mod.config, "model_api_key", lambda: "test-key")

    calls = {"n": 0}

    class Truncated:
        status_code = 200

        def json(self) -> dict:
            calls["n"] += 1
            return {
                "choices": [{"finish_reason": "length", "message": {"content": ""}}],
                "usage": {"cost": 0.0003},
            }

    def fake_post(*args, **kwargs):
        return Truncated()

    original = httpx.post
    httpx.post = fake_post
    try:
        parsed = reply_mod.parse("cancel it. no wait, I'll pay on the 10th. STOP")
    finally:
        httpx.post = original

    assert calls["n"] == 1, "a length finish must not be retried"
    assert parsed.failed
    assert "truncated" in parsed.failure_reason
    assert "max_tokens" in parsed.failure_reason
    assert parsed.cost_usd == 0.0003, "a call that returned nothing was still billed"


def test_a_realigned_retry_is_banded_the_same_way_the_first_one_was(tmp_path) -> None:
    """Issue #30 again, on the path the original fix missed.

    A promise-to-pay reschedules through a second `schedule_retry` call. It did
    not pass the method, so the same card case was unbanded when first scheduled
    and banded when realigned — moved for a regulation that does not reach it,
    and recorded as a WINDOW_VIOLATION that never happened.
    """
    from datetime import date, datetime

    from unhalted.agent import handle_failure
    from unhalted.models import FailureSignal
    from unhalted.shell.scheduler import schedule_retry
    from unhalted.shell.windows import IST
    from unhalted.store import Store

    now = datetime(2026, 9, 3, 11, 0, tzinfo=IST)
    store = Store(str(tmp_path / "realign.db"))
    try:
        case = handle_failure(
            store,
            FailureSignal(
                payment_id="pay_RE", customer_ref="cust_re", amount_paise=49900,
                occurred_at=now, source="test", method="card",
                error_reason="insufficient_funds", error_source="customer",
            ),
            now=now,
        )
        signals = store.signals(case.id)
        assert signals[0].method == "card"

        # Both paths, same target, same method: they must agree.
        target = datetime.combine(date(2026, 9, 4), now.timetz())
        banded = schedule_retry(target, retry_count=0, now=now, method=None)
        carded = schedule_retry(target, retry_count=0, now=now, method="card")

        assert any("WINDOW_VIOLATION" in r for r in banded.rules_fired)
        assert not any("WINDOW_VIOLATION" in r for r in carded.rules_fired), (
            "a card retry must not be moved for a UPI Autopay rule"
        )
    finally:
        store.close()

