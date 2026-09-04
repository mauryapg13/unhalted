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

from unhalted.policy import POLICY

IST = ZoneInfo("Asia/Kolkata")

#: Bands in which NPCI forbids autopay execution, as (start inclusive, end exclusive).
#: Read from config/policy.yaml — see unhalted.policy.
RESTRICTED_BANDS: tuple[tuple[time, time], ...] = POLICY.npci_restricted_bands

#: Hours during which a customer may be contacted, on any channel.
CONTACT_OPEN = POLICY.contact_open
CONTACT_CLOSE = POLICY.contact_close

#: Bumped whenever the rules above change, and recorded on every decision.
WINDOW_RULE_VERSION = POLICY.npci_rule_version

#: Rails the execution bands do **not** govern.
#:
#: The restriction is on UPI Autopay. Recurring card payments are not routed
#: through that rail, and emandate settles through NACH, which has its own cycle
#: and its own bank-holiday shifting. Applying the UPI rule to all three delayed
#: card retries for a regulation that does not reach them and — worse — recorded
#: `WINDOW_VIOLATION` in the audit trail for violations that never happened. See
#: issue #30.
UNBANDED_METHODS = frozenset({"card", "emandate", "nach", "netbanking", "wallet"})


def subject_to_execution_bands(method: str | None) -> bool:
    """Whether NPCI's autopay bands govern this payment method.

    An unknown method is treated as governed. The two mistakes are not
    symmetrical: a card retry delayed for a rule that does not apply costs a few
    hours, and a UPI debit executed inside a restricted band is a regulatory
    breach. When we cannot tell which rail we are on, we take the delay.
    """
    if method is None:
        return True
    return method.lower() not in UNBANDED_METHODS


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


def is_execution_allowed(when: datetime, *, method: str | None = None) -> WindowCheck:
    """Whether `when` falls in an NPCI-permitted autopay execution window.

    `method` decides whether the question applies at all. Omitting it keeps the
    conservative reading, which is what every existing caller wants until it has
    a method to pass.
    """
    if not subject_to_execution_bands(method):
        return WindowCheck(
            allowed=True,
            rule_version=WINDOW_RULE_VERSION,
            reason=f"{method} is not routed through UPI Autopay; no execution band applies",
        )

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


def next_allowed_execution(at_or_after: datetime, *, method: str | None = None) -> datetime:
    """Earliest NPCI-permitted execution time at or after `at_or_after`."""
    when = as_ist(at_or_after)
    check = is_execution_allowed(when, method=method)
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
