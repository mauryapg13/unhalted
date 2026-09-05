"""Terminal formatting, in one place.

Three terminals show this system while it runs — the customer's, the reviewer's,
and the scheduler's — and they are read by somebody watching over a shoulder or
a recording rather than by whoever wrote them. Each had its own copy of the same
escape codes, which is how three views of one system end up looking like three
systems.

Everything here degrades to plain text when stdout is not a terminal, so piping
a view into a file or a test gives readable output rather than escape sequences.
"""

from __future__ import annotations

import re
import shutil
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TypeVar

T = TypeVar("T")

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

BLUE = "\033[38;5;68m"     # the shell: rules, structure, decisions
VIOLET = "\033[38;5;140m"  # the model: perception, drafting
RED = "\033[38;5;167m"     # a refusal, a stop, a hold
GREEN = "\033[38;5;71m"    # permitted, delivered, done
AMBER = "\033[38;5;179m"   # waiting, deferred, due

#: Anything wider than this is harder to read on a recording than it is useful.
MAX_WIDTH = 92

_ANSI = re.compile(r"\033\[[0-9;]*m")


def colour() -> bool:
    return sys.stdout.isatty()


def paint(text: str, *codes: str) -> str:
    if not colour() or not codes:
        return text
    return "".join(codes) + text + RESET


def width() -> int:
    return min(shutil.get_terminal_size((MAX_WIDTH, 24)).columns, MAX_WIDTH)


def visible(text: str) -> int:
    """Length as the eye sees it, so padding survives colour."""
    return len(_ANSI.sub("", text))


def pad(text: str, to: int) -> str:
    return text + " " * max(0, to - visible(text))


def ellipsis(text: str, limit: int) -> str:
    """Shorten to `limit`, on a word boundary, and say that it was shortened.

    A hard slice stops mid-word and reads as a bug rather than as a choice —
    the auditor view ended an outcome on "reschedules to the date the", which
    undermines the one thing that view exists to claim.
    """
    if limit <= 1 or visible(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def field(value: object) -> str:
    """A value as a person reads it. `None` is a Python word, not an answer."""
    if value is None or value == "":
        return "—"
    return str(value)


#: What each confidence band means, in the palette's own terms. Green acts,
#: amber acts and is sampled, red does not act.
AUTHORITY_TINT = {
    "auto-execute": GREEN,
    "auto-execute-sampled-qa": AMBER,
    "hold-for-human": RED,
}


def authority(band: str) -> str:
    """The confidence band, tinted by what it permits."""
    return paint(band, AUTHORITY_TINT.get(band, DIM))


def counter(label: str, value: int, *, tint: str = "") -> str:
    """`label=value`, dimmed when it is zero.

    A run that did one thing prints six counters, four of them zero. Dimming
    the empty ones lets the two that happened carry the line without anything
    moving.
    """
    if value == 0:
        return paint(f"{label}={value}", DIM)
    return f"{paint(label + '=', DIM)}{paint(str(value), tint or BOLD)}"


def clear() -> str:
    """Home the cursor and wipe. Empty when not a terminal, so logs stay logs."""
    return "\033[2J\033[H" if colour() else ""


# -- structure ---------------------------------------------------------------


def banner(title: str, subtitle: str = "") -> str:
    """The block at the top of a view, so a viewer knows which one they are in."""
    w = width()
    bar = "━" * w
    lines = [paint(bar, DIM), paint(f" {title}", BOLD)]
    if subtitle:
        lines.append(paint(f" {subtitle}", DIM))
    lines.append(paint(bar, DIM))
    return "\n".join(lines)


def rule(title: str = "") -> str:
    w = width()
    if not title:
        return paint("─" * w, DIM)
    head = f"── {title} "
    return paint(head + "─" * max(0, w - visible(head)), DIM)


def kv(label: str, value: str, *, label_width: int = 16, tint: str = "") -> str:
    """A label and a value on one line, aligned across every caller."""
    shown = paint(value, tint) if tint else value
    return f"  {paint(pad(label, label_width), DIM)}{shown}"


def chip(text: str, tint: str = BLUE) -> str:
    """A short status marker. Reads at a glance, which is the point on camera."""
    return paint(f" {text} ", tint, BOLD) if colour() else f"[{text}]"


def bullet(text: str, tint: str = "") -> str:
    return f"  {paint('•', DIM)} {paint(text, tint) if tint else text}"


def box(lines: list[str], *, tint: str = "") -> str:
    """A message as it would appear to somebody receiving it."""
    edge = paint("│", tint or DIM)
    top = paint("┌─", tint or DIM)
    bottom = paint("└─", tint or DIM)
    body = "\n".join(f"  {edge} {line}" for line in lines)
    return f"  {top}\n{body}\n  {bottom}"


def table(rows: list[tuple[str, ...]], *, headers: tuple[str, ...] = ()) -> str:
    """Columns that line up. Numbers read wrongly when they do not."""
    if not rows:
        return ""
    cols = len(rows[0])
    widths = [0] * cols
    for row in ([headers] if headers else []) + rows:
        for i, cell in enumerate(row[:cols]):
            widths[i] = max(widths[i], visible(str(cell)))

    out = []
    if headers:
        out.append(
            "  " + "  ".join(paint(pad(h, widths[i]), DIM) for i, h in enumerate(headers))
        )
    for row in rows:
        out.append("  " + "  ".join(pad(str(c), widths[i]) for i, c in enumerate(row[:cols])))
    return "\n".join(out)


# -- time --------------------------------------------------------------------


def relative(when: datetime | None, now: datetime) -> str:
    """"in 15h 48m", "4m ago". A timestamp alone makes a viewer do arithmetic."""
    if when is None:
        return "unscheduled"
    delta = when - now
    ahead = delta > timedelta(0)
    seconds = int(abs(delta).total_seconds())

    if seconds < 60:
        span = f"{seconds}s"
    elif seconds < 3600:
        span = f"{seconds // 60}m"
    elif seconds < 86400:
        span = f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"
    else:
        span = f"{seconds // 86400}d {(seconds % 86400) // 3600:02d}h"

    return f"in {span}" if ahead else f"{span} ago"


def clock(when: datetime) -> str:
    return f"{when:%d %b %H:%M}"


# -- a wait that says so ------------------------------------------------------
#
# A blocking call with nothing printed first reads as a hang, not a wait — see
# BREAKAGE.md's entry on `scripts/propose_policy_change.py`. `spin` is the fix
# generalised: a label shimmers while `fn` runs on a background thread, so
# anything waiting on the model looks like it's doing something, and the wait
# ends the moment `fn` actually returns rather than after a fixed delay.

_SPIN_WIDTH = 2
_SPIN_FPS = 12.0


def _shimmer(label: str, pos: int) -> str:
    out = []
    for i, ch in enumerate(label):
        dist = abs(i - pos)
        if ch == " ":
            out.append(ch)
        elif dist == 0:
            out.append(paint(ch, BOLD, VIOLET))
        elif dist <= _SPIN_WIDTH:
            out.append(paint(ch, VIOLET))
        else:
            out.append(paint(ch, DIM))
    return "".join(out)


def spin(label: str, fn: Callable[[], T]) -> T:
    """Run `fn` to completion, showing `label` shimmering while it does.

    Off a terminal, prints `label` once as a plain static line — a recording
    or a piped log gets one readable line, not a stream of carriage returns.
    `fn`'s exception, if any, propagates from here exactly as it would from a
    direct call; nothing here swallows or reframes a failure.
    """
    if not colour():
        print(f"  {label}", flush=True)
        return fn()

    done = threading.Event()
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised on the caller's thread below
            box["error"] = e
        finally:
            done.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()

    frame = 0
    n = max(len(label), 1)
    try:
        while not done.wait(timeout=1 / _SPIN_FPS):
            print(f"\r  {_shimmer(label, frame % n)}", end="", flush=True)
            frame += 1
    finally:
        worker.join()
        print("\r" + " " * (len(label) + 2) + "\r", end="", flush=True)

    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["value"]  # type: ignore[return-value]
