"""Terminal formatting.

Only the parts that can be wrong in a way that matters: padding that colour
breaks, relative times that read backwards, and output that must stay plain
when nobody is looking at a terminal.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from unhalted import tui
from unhalted.shell.windows import IST

NOW = datetime(2026, 9, 3, 11, 0, tzinfo=IST)


def test_colour_is_off_when_stdout_is_not_a_terminal() -> None:
    """Piping a view into a file or a test should give text, not escape codes."""
    assert tui.colour() is False
    assert tui.paint("hello", tui.RED) == "hello"
    assert tui.clear() == ""


def test_a_chip_stays_readable_without_colour() -> None:
    assert tui.chip("CANCELLED", tui.RED) == "[CANCELLED]"


def test_visible_length_ignores_escape_codes() -> None:
    """Padding is computed from what the eye sees, or columns drift once any
    cell is coloured."""
    coloured = "\033[1mCASE-ABCD\033[0m"
    assert tui.visible(coloured) == len("CASE-ABCD")
    assert len(coloured) > tui.visible(coloured)


def test_padding_uses_visible_length() -> None:
    coloured = "\033[1mab\033[0m"
    padded = tui.pad(coloured, 5)
    assert tui.visible(padded) == 5


def test_a_table_lines_its_columns_up() -> None:
    out = tui.table(
        [("1.", "CASE-A", "Rs 499"), ("2.", "CASE-LONGER", "Rs 1,299")],
        headers=("", "case", "amount"),
    )
    lines = out.splitlines()
    assert len(lines) == 3
    starts = [line.index("Rs") for line in lines[1:]]
    assert starts[0] == starts[1], "the amount column moved between rows"


def test_an_empty_table_is_empty_rather_than_a_header_alone() -> None:
    assert tui.table([], headers=("a", "b")) == ""


@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(seconds=20), "in 20s"),
        (timedelta(minutes=4), "in 4m"),
        (timedelta(hours=16, minutes=5), "in 16h 05m"),
        (timedelta(days=1, hours=15), "in 1d 15h"),
        (timedelta(minutes=-3), "3m ago"),
        (timedelta(days=-2), "2d 00h ago"),
    ],
)
def test_relative_time_reads_the_way_a_person_would_say_it(delta, expected) -> None:
    assert tui.relative(NOW + delta, NOW) == expected


def test_an_unscheduled_action_says_so_rather_than_showing_a_time() -> None:
    assert tui.relative(None, NOW) == "unscheduled"


def test_width_is_capped_so_a_recording_stays_legible() -> None:
    assert tui.width() <= tui.MAX_WIDTH


def test_a_rule_fills_the_width_it_is_given() -> None:
    assert tui.visible(tui.rule()) == tui.width()
    assert tui.visible(tui.rule("a title")) == tui.width()


def test_a_box_frames_every_line_it_is_given() -> None:
    out = tui.box(["one", "two"])
    assert out.count("│") == 2
    assert "┌" in out
    assert "└" in out


# -- spin ---------------------------------------------------------------------


def test_off_a_terminal_spin_prints_the_label_once_and_returns_the_result() -> None:
    """No animation possible without colour; a static line instead of nothing,
    the same reasoning `paint` and `clear` already use."""
    assert tui.spin("analyzing", lambda: 42) == 42


def test_spin_returns_what_fn_returns_even_when_animating(monkeypatch) -> None:
    monkeypatch.setattr(tui, "colour", lambda: True)
    assert tui.spin("analyzing", lambda: "the answer") == "the answer"


def test_spin_propagates_fns_exception_rather_than_swallowing_it(monkeypatch) -> None:
    monkeypatch.setattr(tui, "colour", lambda: True)

    def boom():
        raise ValueError("the model call failed")

    with pytest.raises(ValueError, match="the model call failed"):
        tui.spin("analyzing", boom)


def test_spin_propagates_an_exception_off_a_terminal_too() -> None:
    def boom():
        raise ValueError("still fails")

    with pytest.raises(ValueError, match="still fails"):
        tui.spin("analyzing", boom)


def test_shimmer_never_colours_a_space(monkeypatch) -> None:
    monkeypatch.setattr(tui, "colour", lambda: True)
    frame = tui._shimmer("a b", 1)
    # the space must survive untouched between the two letters' escape codes
    assert "m m" not in frame  # a coloured space would show a code either side
    assert " " in frame


def test_shimmer_highlights_exactly_one_position_as_bold(monkeypatch) -> None:
    monkeypatch.setattr(tui, "colour", lambda: True)
    frame = tui._shimmer("abcde", 2)
    # BOLD+VIOLET wraps exactly the character at the sweep position ('c')
    assert tui.paint("c", tui.BOLD, tui.VIOLET) in frame
    for other in "abde":
        assert tui.paint(other, tui.BOLD, tui.VIOLET) not in frame
