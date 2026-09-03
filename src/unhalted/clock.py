"""Stating the time explicitly, loudly.

Every decision in this system takes `now` as an argument — the scheduler, the
window rules, the runner, the agent loop — because a rule about NPCI's execution
bands is untestable if it can only ever read the wall clock. The 333 tests all
pass a fixed instant.

The scripts did not. So the window rules could be exercised from a test and not
from a rehearsal, and checking a run sheet meant waiting until 10am.

Why this is not a demo switch
-----------------------------
`CHECKPOINTS.md` rules out any code path that exists only to make the video work,
and it is worth being precise about why this is not one. Nothing here changes
behaviour: the same rules run, the same refusals fire, the same rows are written.
The only difference is which instant they are evaluated against — which is
exactly what the test suite already does, and what dependency injection is for.

The risk is not the capability, it is using it **silently** while filming and
letting a stated time pass for a real one. So an override announces itself on
stdout, where a recording would capture it. The safety is visibility rather than
absence, the same way `capabilities` reports what a deployment cannot do instead
of hiding it.
"""

from __future__ import annotations

from datetime import datetime

from unhalted.shell import windows

#: Accepted forms, most convenient first. A bare time is not accepted: "11:00"
#: with no date would silently mean today, and a rehearsal that quietly rolls
#: over midnight is worse than one that refuses to start.
FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S")


class BadTime(ValueError):
    """The stated time could not be read."""


def parse(text: str) -> datetime:
    """Read a stated time as IST.

    Naive input is treated as IST rather than UTC, because every rule this
    system holds is Indian and a rehearsal that silently shifts by five and a
    half hours would land on the wrong side of a restricted band.
    """
    for fmt in FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=windows.IST)
        except ValueError:
            continue
    try:
        return windows.as_ist(datetime.fromisoformat(text))
    except ValueError as exc:
        raise BadTime(
            f"could not read {text!r} as a time. Try '2026-09-04 11:00' (IST)"
        ) from exc


def banner(stated: datetime, *, real: datetime | None = None) -> str:
    """The line that makes an override impossible to use by accident."""
    real = real or datetime.now(tz=windows.IST)
    return (
        "\n"
        "  !! CLOCK OVERRIDDEN\n"
        f"     rules evaluate against  {stated:%Y-%m-%d %H:%M %Z}\n"
        f"     the real time is        {real:%Y-%m-%d %H:%M %Z}\n"
        "     For rehearsal and testing. Not for recording.\n"
    )


def resolve(at: str | None) -> tuple[datetime, str | None]:
    """Return the instant to reason about, and a banner if it was stated.

    `(now, None)` when nothing was overridden, so a caller that forgets to print
    the banner still cannot produce one by accident.
    """
    if at is None:
        return datetime.now(tz=windows.IST), None
    stated = parse(at)
    return stated, banner(stated)
