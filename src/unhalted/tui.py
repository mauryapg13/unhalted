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
from datetime import datetime, timedelta

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
