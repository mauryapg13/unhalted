"""The shell's timing rules.

These encode NPCI's restricted execution bands and our contact hours. If any of
these fail, the shell is permitting a debit or a message that regulation or
policy forbids.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from unhalted.shell.windows import (
    IST,
    is_contact_allowed,
    is_execution_allowed,
    next_allowed_contact,
    next_allowed_execution,
)


def ist(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


@pytest.mark.parametrize(
    ("hour", "minute", "allowed"),
    [
        (9, 59, True),  # just before the morning band opens
        (10, 0, False),  # morning band, inclusive start
        (10, 42, False),  # the failure time used throughout the spec
        (12, 59, False),
        (13, 0, True),  # morning band, exclusive end
        (16, 59, True),
        (17, 0, False),  # evening band — the one missing from the original spec
        (18, 30, False),  # the recommendation the shell must refuse
        (21, 29, False),
        (21, 30, True),  # evening band, exclusive end
        (23, 45, True),
    ],
)
def test_execution_windows(hour: int, minute: int, allowed: bool) -> None:
    assert is_execution_allowed(ist(2026, 9, 1, hour, minute)).allowed is allowed


def test_refusal_names_the_band_it_violated() -> None:
    check = is_execution_allowed(ist(2026, 9, 1, 18, 30))
    assert check.allowed is False
    assert check.code == "WINDOW_VIOLATION"
    assert "17:00-21:30" in check.reason
    assert check.rule_version


@pytest.mark.parametrize(
    ("hour", "minute", "expected_hour", "expected_minute"),
    [
        (10, 42, 13, 0),  # morning band defers to its end
        (18, 30, 21, 30),  # evening band defers to its end
        (14, 0, 14, 0),  # already permitted, unchanged
        (23, 0, 23, 0),
    ],
)
def test_next_allowed_execution(
    hour: int, minute: int, expected_hour: int, expected_minute: int
) -> None:
    got = next_allowed_execution(ist(2026, 9, 1, hour, minute))
    assert (got.hour, got.minute) == (expected_hour, expected_minute)


def test_naive_datetimes_are_treated_as_ist() -> None:
    naive = datetime(2026, 9, 1, 11, 0)  # noqa: DTZ001 — a naive input is the point of this test
    assert is_execution_allowed(naive).allowed is False


def test_utc_input_is_converted_before_the_rule_is_applied() -> None:
    """05:12 UTC is 10:42 IST, which is restricted. Timezone bugs cost money."""
    utc = datetime(2026, 9, 1, 5, 12, tzinfo=UTC)
    assert is_execution_allowed(utc).allowed is False


@pytest.mark.parametrize(
    ("hour", "minute", "allowed"),
    [(7, 59, False), (8, 0, True), (18, 58, True), (19, 0, False), (23, 0, False)],
)
def test_contact_hours(hour: int, minute: int, allowed: bool) -> None:
    assert is_contact_allowed(ist(2026, 9, 1, hour, minute)).allowed is allowed


def test_message_retry_after_hours_defers_to_next_morning() -> None:
    """From the specification: a send failing at 18:58 must not retry at 19:05."""
    got = next_allowed_contact(ist(2026, 9, 1, 19, 5))
    assert (got.date().day, got.hour, got.minute) == (2, 8, 0)


def test_contact_before_opening_defers_to_the_same_morning() -> None:
    got = next_allowed_contact(ist(2026, 9, 1, 6, 30))
    assert (got.date().day, got.hour) == (1, 8)


# --- issue #30: the bands govern one rail, not three ------------------------


@pytest.mark.parametrize("method", ["card", "emandate", "nach", "netbanking", "wallet"])
def test_methods_outside_upi_autopay_are_not_banded(method) -> None:
    """Cards are not NPCI-routed for recurring; emandate settles through NACH."""
    inside = ist(2026, 9, 1, 11, 0)
    assert is_execution_allowed(inside, method=method).allowed
    assert next_allowed_execution(inside, method=method) == inside


def test_upi_is_banded() -> None:
    inside = ist(2026, 9, 1, 11, 0)
    assert not is_execution_allowed(inside, method="upi").allowed


def test_an_unknown_method_takes_the_delay_rather_than_the_risk() -> None:
    """A card retry delayed wrongly costs hours. A UPI debit executed inside a
    band is a breach. With no method to go on, take the delay."""
    inside = ist(2026, 9, 1, 11, 0)
    assert not is_execution_allowed(inside, method=None).allowed
    assert not is_execution_allowed(inside).allowed


def test_an_unbanded_method_says_why_it_was_allowed() -> None:
    check = is_execution_allowed(ist(2026, 9, 1, 11, 0), method="card")
    assert "not routed through UPI Autopay" in check.reason
    assert check.code is None
