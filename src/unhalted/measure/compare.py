"""One case, under both policies.

The batch report answers "what do these two policies do across 300 failures".
This answers the same question about a single real case, which is the question
a merchant actually asks first and the one a person can check by hand.

Both sides are read, never invented. The agent's column comes out of the audit
trail — the only account of what happened that anyone should trust — and the
baseline's comes out of `measure/baseline.py`, which replays what Razorpay's
subscription documentation says happens today.

What this deliberately does not do
----------------------------------
Say what either policy recovered. Deciding whether a given retry would have
worked is an outcome model, and whoever writes one decides the comparison. The
batch report splits in two for that reason; this stops at part one and has no
part two. Attempts spent, attempts that provably cannot work, bands violated,
customers contacted — all countable, none of them needing to know how the story
ended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from unhalted.measure import baseline
from unhalted.models import AuditRecord, Case, Diagnosis, DiagnosisClass, FailureSignal
from unhalted.shell import windows

#: Audit actions that mean a debit was actually put on the calendar.
_SCHEDULED = ("retry at", "retry realigned to")

#: Short markers, because each attempt has to fit on one line. Spelled out
#: underneath the table rather than left as jargon.
CANNOT_WORK = "cannot work"
NPCI_BAND = "NPCI band"

LEGEND = {
    CANNOT_WORK: "Razorpay's own error description rules a retry out",
    NPCI_BAND: "10:00-13:00 or 17:00-21:30 IST, when execution is not permitted",
}


@dataclass(frozen=True)
class Event:
    """One moment, as each policy saw it.

    Either side may be empty: the baseline does nothing at all between its
    retries, and the agent frequently does something the baseline has no
    concept of.
    """

    at: datetime | None
    baseline: str
    agent: str
    #: Set when the line is the interesting one — a debit the baseline spends
    #: that the agent does not, or a rule the shell enforced.
    marked: bool = False


@dataclass
class Side:
    attempts: int = 0
    futile_attempts: int = 0
    attempts_in_restricted_window: int = 0
    contacts: int = 0
    #: Attempts the shell moved out of a restricted band rather than cancelling.
    corrected_into_window: int = 0
    outcome: str = ""


@dataclass
class Comparison:
    case_id: str
    amount_paise: int
    customer_ref: str
    signal: FailureSignal | None
    diagnosis: Diagnosis | None
    events: list[Event] = field(default_factory=list)
    agent: Side = field(default_factory=Side)
    base: Side = field(default_factory=Side)

    @property
    def amount_rupees(self) -> float:
        return self.amount_paise / 100


def _agent_side(
    timeline: list[AuditRecord],
    klass: DiagnosisClass | None,
    state: str,
) -> tuple[list[Event], Side]:
    """Read the agent's column straight off the audit trail."""
    side = Side()
    events: list[Event] = []

    for record in timeline:
        text = record.action
        marked = False

        if record.decision_type == "schedule" and text.startswith(_SCHEDULED):
            side.attempts += 1
            if klass in baseline.FUTILE:
                side.futile_attempts += 1
            # The shell moves a retry out of a restricted band rather than
            # dropping it, and records that it had to. So a violation in
            # `rules_fired` is an attempt corrected, not an attempt spent.
            if any(r.startswith("WINDOW_VIOLATION") for r in record.rules_fired):
                side.corrected_into_window += 1
                marked = True

        if record.decision_type == "escalation":
            cost = (record.inputs or {}).get("cost_paise") or 0
            if cost:
                side.contacts += 1

        if record.decision_type == "stop":
            marked = True

        events.append(Event(at=record.at, baseline="", agent=text, marked=marked))

    side.outcome = state
    return events, side


def _baseline_side(
    signal: FailureSignal, klass: DiagnosisClass, anchor: datetime
) -> tuple[list[Event], Side]:
    """Replay the documented policy and describe each attempt it spends.

    Anchored to the moment the agent saw the failure, not to the payment's own
    timestamp. A captured fixture carries the clock of the day it was captured,
    and two columns on two different clocks compare nothing.
    """
    signal = signal.model_copy(update={"occurred_at": anchor})
    run = baseline.run(signal, klass)
    side = Side(
        attempts=run.attempts,
        futile_attempts=run.futile_attempts,
        attempts_in_restricted_window=run.attempts_in_restricted_window,
        contacts=0,
        outcome="halted",
    )

    events: list[Event] = []
    at = windows.as_ist(signal.occurred_at)
    for n in range(1, run.attempts + 1):
        at = at + timedelta(days=1)
        # Kept to one line each. Three annotated attempts wrapping over nine
        # lines buries the only thing this view exists to show.
        marks = []
        if klass in baseline.FUTILE:
            marks.append(CANNOT_WORK)
        if not windows.is_execution_allowed(at, method=signal.method).allowed:
            marks.append(NPCI_BAND)
        note = f"DEBIT ATTEMPT {n}/{run.attempts}"
        if marks:
            note += "  " + " · ".join(marks)
        events.append(Event(at=at, baseline=note, agent="", marked=True))

    events.append(
        Event(at=at, baseline="HALTED — no diagnosis, no contact, no memory", agent="")
    )
    return events, side


def compare(
    case: Case,
    signals: list[FailureSignal],
    diagnosis: Diagnosis | None,
    timeline: list[AuditRecord],
) -> Comparison:
    """Build the two columns for one case.

    `diagnosis` is used only to say how many of the baseline's attempts were
    spent on a failure a retry cannot fix. The baseline itself never sees it —
    not having a diagnosis is the thing being compared against.
    """
    signal = signals[0] if signals else None
    klass = diagnosis.klass if diagnosis else DiagnosisClass.UNKNOWN

    agent_events, agent = _agent_side(timeline, klass, case.state.value)

    if signal is None:
        base_events, base = [], Side(outcome="not comparable — no signal stored")
    else:
        # T=0 is when this system saw the failure, which is where the audit
        # trail starts. Both policies react to the same event at the same time.
        anchor = timeline[0].at if timeline else case.opened_at
        base_events, base = _baseline_side(signal, klass, anchor)

    events = sorted(
        agent_events + base_events,
        key=lambda e: (e.at is None, e.at or datetime.min.replace(tzinfo=UTC)),
    )

    return Comparison(
        case_id=case.id,
        amount_paise=case.amount_paise,
        customer_ref=case.customer_ref,
        signal=signal,
        diagnosis=diagnosis,
        events=events,
        agent=agent,
        base=base,
    )


def differences(comparison: Comparison) -> list[tuple[str, int, int, str]]:
    """The counted summary: (label, agent, baseline, note).

    Every number here is a count of something one of the policies did. None of
    them rests on knowing whether anything worked.
    """
    a, b = comparison.agent, comparison.base
    return [
        (
            "Debit attempts",
            a.attempts,
            b.attempts,
            f"{b.attempts - a.attempts} fewer" if b.attempts > a.attempts else "",
        ),
        (
            "Attempts a retry cannot fix",
            a.futile_attempts,
            b.futile_attempts,
            f"{b.futile_attempts - a.futile_attempts} avoided"
            if b.futile_attempts > a.futile_attempts
            else "",
        ),
        (
            "Attempts inside NPCI bands",
            a.attempts_in_restricted_window,
            b.attempts_in_restricted_window,
            f"{b.attempts_in_restricted_window} avoided"
            if b.attempts_in_restricted_window
            else "",
        ),
        (
            "Customer contacts",
            a.contacts,
            b.contacts,
            "the baseline never contacts anyone",
        ),
    ]
