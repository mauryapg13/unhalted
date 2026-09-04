"""The report a human actually reads. The one property that matters most is
already tested in core/policy_change.py; this is about what gets printed.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib

import pytest

from unhalted.core.policy_change import Proposal, ProposedChange

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "propose_policy_change.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("propose_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_failed_read_says_so_plainly(script) -> None:
    result = Proposal((), (), (), 0.0, failed=True, failure_reason="no model API key configured")
    out = script.render(result)
    assert "could not read this text" in out
    assert "no model API key configured" in out


def test_a_proposed_change_shows_current_and_proposed_and_the_quote(script) -> None:
    change = ProposedChange(
        field="retries.cap", current_value=3, proposed_value=4,
        quote="the retry cap is increased to four attempts",
        reasoning="the circular states a new cap",
    )
    result = Proposal((change,), (), (), 0.0004)
    out = script.render(result)
    assert "retries.cap" in out
    assert "current:   3" in out
    assert "proposed:  4" in out
    assert "the retry cap is increased to four attempts" in out
    assert "NOT APPLIED" in out


def test_dropped_and_unclear_both_appear(script) -> None:
    result = Proposal(
        (), ("something vague",), ("field.not.allowed: refused",), 0.0001,
    )
    out = script.render(result)
    assert "field.not.allowed: refused" in out
    assert "something vague" in out


def test_nothing_here_writes_to_the_policy_file(script) -> None:
    import inspect
    source = inspect.getsource(script)
    assert "write_text" not in source
    assert "yaml.safe_dump" not in source


def test_the_report_always_says_nothing_was_written(script) -> None:
    result = Proposal((), (), (), 0.0)
    out = script.render(result)
    assert "nothing has been written" in out


class _FakeStdin(io.StringIO):
    """io.StringIO with a controllable isatty(), which the real object
    doesn't let you fake."""

    def __init__(self, text: str, *, tty: bool):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_an_interactive_terminal_is_told_it_is_waiting(script, monkeypatch, capsys) -> None:
    """A wait with nothing on screen reads as a hang, not a wait — the exact
    bug the reviewer terminal had before show_case stopped blocking on the
    model. This is that fix's counterpart here."""
    monkeypatch.setattr(script.sys, "argv", ["propose_policy_change.py"])
    monkeypatch.setattr(script.sys, "stdin", _FakeStdin("some text", tty=True))
    monkeypatch.setattr(script, "propose", lambda text: Proposal((), (), (), 0.0))

    script.main()

    assert "press Ctrl-D" in capsys.readouterr().err


def test_piped_input_gets_no_prompt(script, monkeypatch, capsys) -> None:
    monkeypatch.setattr(script.sys, "argv", ["propose_policy_change.py"])
    monkeypatch.setattr(script.sys, "stdin", _FakeStdin("some text", tty=False))
    monkeypatch.setattr(script, "propose", lambda text: Proposal((), (), (), 0.0))

    script.main()

    assert capsys.readouterr().err == ""


def test_a_file_argument_never_touches_stdin_at_all(script, monkeypatch, tmp_path) -> None:
    circular = tmp_path / "circular.txt"
    circular.write_text("some text")
    monkeypatch.setattr(script.sys, "argv", ["propose_policy_change.py", "--file", str(circular)])

    class ExplodingStdin:
        def read(self):
            raise AssertionError("must not read stdin when --file is given")

        def isatty(self):
            raise AssertionError("must not check stdin when --file is given")

    monkeypatch.setattr(script.sys, "stdin", ExplodingStdin())
    monkeypatch.setattr(script, "propose", lambda text: Proposal((), (), (), 0.0))

    script.main()  # would raise via ExplodingStdin if this regresses
