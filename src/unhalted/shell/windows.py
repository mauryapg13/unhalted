"""NPCI execution windows and customer contact hours.

Deterministic shell. Nothing here consults a model. These are regulatory facts
encoded as code, so that no recommendation at any confidence can route around
them.

NPCI restricts UPI Autopay execution to non-peak hours. The restricted bands are
10:00-13:00 and 17:00-21:30 IST; execution is permitted before 10:00, between
13:00-17:00, and after 21:30.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

#: Bands in which NPCI forbids autopay execution, as (start inclusive, end exclusive).
RESTRICTED_BANDS: tuple[tuple[time, time], ...] = (
    (time(10, 0), time(13, 0)),
    (time(17, 0), time(21, 30)),
)

#: Hours during which a customer may be contacted, on any channel.
CONTACT_OPEN = time(8, 0)
CONTACT_CLOSE = time(19, 0)

#: Bumped whenever the rules above change, and recorded on every decision.
WINDOW_RULE_VERSION = "npci-2025-08-01"


@dataclass(frozen=True)
class WindowCheck:
    """Why a candidate time was allowed or refused."""

    allowed: bool
    rule_version: str
    reason: str
    band: tuple[time, time] | None = None

    @property
    def code(self) -> str | None:
        return None if self.allowed else "WINDOW_VIOLATION"


def as_ist(when: datetime) -> datetime:
    """Coerce to IST. Naive datetimes are assumed to already be IST."""
    return when.replace(tzinfo=IST) if when.tzinfo is None else when.astimezone(IST)


def is_execution_allowed(when: datetime) -> WindowCheck:
    """Whether `when` falls in an NPCI-permitted autopay execution window."""
    t = as_ist(when).time()
    for start, end in RESTRICTED_BANDS:
        if start <= t < end:
            return WindowCheck(
                allowed=False,
                rule_version=WINDOW_RULE_VERSION,
                reason=f"NPCI restricted execution window {start:%H:%M}-{end:%H:%M} IST",
                band=(start, end),
            )
    return WindowCheck(
        allowed=True,
        rule_version=WINDOW_RULE_VERSION,
        reason="outside all NPCI restricted execution windows",
    )


def next_allowed_execution(at_or_after: datetime) -> datetime:
    """Earliest NPCI-permitted execution time at or after `at_or_after`."""
    when = as_ist(at_or_after)
    check = is_execution_allowed(when)
    if check.allowed:
        return when
    assert check.band is not None
    _, end = check.band
    return when.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)


def is_contact_allowed(when: datetime) -> WindowCheck:
    """Whether `when` falls inside permitted customer-contact hours."""
    t = as_ist(when).time()
    if CONTACT_OPEN <= t < CONTACT_CLOSE:
        return WindowCheck(
            allowed=True,
            rule_version=WINDOW_RULE_VERSION,
            reason="inside contact hours",
        )
    return WindowCheck(
        allowed=False,
        rule_version=WINDOW_RULE_VERSION,
        reason=f"outside contact hours {CONTACT_OPEN:%H:%M}-{CONTACT_CLOSE:%H:%M} IST",
        band=(CONTACT_OPEN, CONTACT_CLOSE),
    )


def next_allowed_contact(at_or_after: datetime) -> datetime:
    """Earliest permitted contact time at or after `at_or_after`."""
    when = as_ist(at_or_after)
    if is_contact_allowed(when).allowed:
        return when
    day = when.date()
    if when.time() >= CONTACT_CLOSE:
        day = day + timedelta(days=1)
    return datetime.combine(day, CONTACT_OPEN, tzinfo=IST)
