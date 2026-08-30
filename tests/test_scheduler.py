"""The shell's scheduling decisions, including the ones that refuse a request."""

from __future__ import annotations

from datetime import datetime

from unhalted.shell.scheduler import RETRY_CAP, schedule_retry
from unhalted.shell.windows import IST


def ist(hour: int, minute: int = 0, day: int = 1) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=IST)


def test_a_permitted_time_is_honoured_unchanged() -> None:
    d = schedule_retry(ist(14, 0), retry_count=0)
    assert d.accepted and d.scheduled_for == ist(14, 0)
    assert not d.was_moved
    assert d.rules_fired == []


def test_evening_recommendation_is_moved_out_of_the_restricted_band() -> None:
    """The demo beat: a plausible 18:30 request that NPCI forbids."""
    d = schedule_retry(ist(18, 30), retry_count=0)
    assert d.accepted
    assert d.was_moved
    assert d.scheduled_for == ist(21, 30)
    assert any(r.startswith("WINDOW_VIOLATION") for r in d.rules_fired)
    assert "17:00-21:30" in d.rules_fired[0]


def test_morning_band_request_moves_to_thirteen_hundred() -> None:
    d = schedule_retry(ist(10, 42), retry_count=0)
    assert d.scheduled_for == ist(13, 0)
    assert any(r.startswith("WINDOW_VIOLATION") for r in d.rules_fired)


def test_retry_cap_cannot_be_exceeded() -> None:
    d = schedule_retry(ist(14, 0), retry_count=RETRY_CAP)
    assert not d.accepted
    assert d.scheduled_for is None
    assert d.rules_fired == ["STOP_RULE:RETRY_CAP"]


def test_the_cap_holds_however_the_request_was_justified() -> None:
    """There is no confidence, and no caller, that raises the cap."""
    for count in (RETRY_CAP, RETRY_CAP + 1, RETRY_CAP + 10):
        assert schedule_retry(ist(14, 0), retry_count=count).accepted is False


def test_a_time_in_the_past_is_pulled_forward_to_now() -> None:
    d = schedule_retry(ist(14, 0), retry_count=0, now=ist(15, 30))
    assert d.scheduled_for == ist(15, 30)
    assert "NOT_IN_THE_PAST" in d.rules_fired


def test_a_past_time_pulled_into_a_restricted_band_is_then_moved_out() -> None:
    """Two rules composing: pulled forward to 18:00, then out of the evening band."""
    d = schedule_retry(ist(14, 0), retry_count=0, now=ist(18, 0))
    assert d.scheduled_for == ist(21, 30)
    assert "NOT_IN_THE_PAST" in d.rules_fired
    assert any(r.startswith("WINDOW_VIOLATION") for r in d.rules_fired)


def test_every_decision_carries_the_rule_version_that_produced_it() -> None:
    assert schedule_retry(ist(14, 0), retry_count=0).rule_version
