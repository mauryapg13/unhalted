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


# -- backoff ------------------------------------------------------------------


def test_a_technical_failure_is_not_retried_into_the_same_outage() -> None:
    """Issue #4: retrying in the same second burns an attempt on a live outage."""
    from unhalted.models import DiagnosisClass
    from unhalted.shell.scheduler import backoff_for

    assert backoff_for(DiagnosisClass.RECOVERABLE_TECHNICAL, 0).total_seconds() > 0


def test_backoff_grows_with_each_attempt() -> None:
    from unhalted.models import DiagnosisClass
    from unhalted.shell.scheduler import backoff_for

    waits = [backoff_for(DiagnosisClass.RECOVERABLE_TECHNICAL, n) for n in range(3)]
    assert waits == sorted(waits)
    assert waits[0] < waits[-1]


def test_a_balance_failure_waits_for_money_to_arrive() -> None:
    """Half an hour does not change an empty account. A day might."""
    from datetime import timedelta

    from unhalted.models import DiagnosisClass
    from unhalted.shell.scheduler import backoff_for

    assert backoff_for(DiagnosisClass.RECOVERABLE_BALANCE, 0) >= timedelta(days=1)


def test_a_notification_gap_waits_the_twenty_five_hours_razorpay_requires() -> None:
    from datetime import timedelta

    from unhalted.models import DiagnosisClass
    from unhalted.shell.scheduler import backoff_for

    assert backoff_for(DiagnosisClass.NOTIFICATION_GAP, 0) >= timedelta(hours=25)


def test_backoff_past_the_last_step_holds_at_the_longest_wait() -> None:
    from unhalted.models import DiagnosisClass
    from unhalted.shell.scheduler import backoff_for

    last = backoff_for(DiagnosisClass.RECOVERABLE_TECHNICAL, 2)
    assert backoff_for(DiagnosisClass.RECOVERABLE_TECHNICAL, 99) == last


def test_a_promised_date_is_not_pushed_past_by_backoff() -> None:
    """A retry realigned to a date the customer named lands on that date.

    schedule_retry honours the time it is given; backoff is the caller's to add,
    and a caller honouring a promise does not add it.
    """
    promised = ist(9, 0, day=2)
    d = schedule_retry(promised, retry_count=1, now=ist(22, 0, day=1))
    assert d.scheduled_for == promised
