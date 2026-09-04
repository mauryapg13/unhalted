"""The command line, which is how anybody reads what the agent did.

Reading the audit trail used to mean writing a query. These assert that it no
longer does, and that what prints is the same data the store holds rather than a
prettier summary of it.
"""

from __future__ import annotations

import importlib
import pathlib
from datetime import datetime

import pytest

from unhalted import cli
from unhalted import policy as policy_mod
from unhalted.agent import apply_stop, handle_failure
from unhalted.models import FailureSignal
from unhalted.shell.windows import IST
from unhalted.store import Store

NOW = datetime(2026, 9, 3, 11, 30, tzinfo=IST)


def signal(payment_id="pay_CLI", reason="insufficient_funds", customer="cust_cli"):
    return FailureSignal(
        payment_id=payment_id, customer_ref=customer, amount_paise=49900,
        occurred_at=NOW, source="test", method="card",
        error_reason=reason, error_source="customer",
    )


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "cli.db")
    store = Store(path)
    case = handle_failure(store, signal(), now=NOW)
    store.close()
    return path, case.id


def run(args, capsys) -> str:
    cli.main(args)
    return capsys.readouterr().out


def test_a_case_prints_its_whole_timeline(db, capsys) -> None:
    path, case_id = db
    out = run(["--db", path, "case", case_id], capsys)

    for expected in ("INGEST", "DIAGNOSIS", "ESCALATION", "SCHEDULE"):
        assert expected in out, f"{expected} missing from the timeline"
    assert "recoverable-balance" in out
    assert "razorpay-docs@" in out, "the taxonomy version must be visible"
    assert "pay_CLI" in out


def test_a_case_can_be_named_by_prefix(db, capsys) -> None:
    """The queue prints ids; typing one should be enough."""
    path, case_id = db
    short = case_id.removeprefix("CASE-")[:4]
    assert case_id in run(["--db", path, "case", short], capsys)


def test_an_unknown_case_says_so_and_fails(db, capsys) -> None:
    path, _ = db
    assert cli.main(["--db", path, "case", "CASE-NOPE"]) == 1
    assert "no case matching" in capsys.readouterr().out


def test_the_rule_that_fired_is_shown_not_just_the_outcome(db, capsys) -> None:
    """A decision without its rule is not auditable."""
    path, case_id = db
    out = run(["--db", path, "case", case_id], capsys)
    assert "WINDOW_VIOLATION" in out or "npci-" in out


def test_pending_actions_are_shown(db, capsys) -> None:
    """The line that exposed a scheduled retry nothing could cancel."""
    path, case_id = db
    out = run(["--db", path, "case", case_id], capsys)
    assert "pending" in out
    assert "retry" in out


def test_listing_cases_shows_state_and_diagnosis(db, capsys) -> None:
    path, case_id = db
    out = run(["--db", path, "cases"], capsys)
    assert case_id in out and "open" in out and "recoverable-balance" in out


def test_cases_can_be_filtered_by_state(db, capsys) -> None:
    path, _ = db
    assert "no cases in state" in run(["--db", path, "cases", "--state", "recovered"], capsys)


def test_an_empty_queue_says_nothing_is_waiting(db, capsys) -> None:
    path, _ = db
    assert "nothing is waiting" in run(["--db", path, "queue"], capsys)


def test_the_queue_shows_held_cases_and_why(tmp_path, capsys) -> None:
    path = str(tmp_path / "q.db")
    store = Store(path)
    case = handle_failure(store, signal(reason="payment_risk_check_failed"), now=NOW)
    apply_stop(store, "DISPUTE", case_id=case.id, customer_ref=case.customer_ref, now=NOW)
    store.close()

    out = run(["--db", path, "queue"], capsys)
    assert case.id in out
    assert "DISPUTE" in out


def test_capabilities_reports_what_is_absent_not_only_what_works(capsys) -> None:
    """An absence should be inspectable rather than a silent hole."""
    out = run(["capabilities"], capsys)
    assert "UPI Autopay transport: absent" in out
    assert "taxonomy" in out.lower()


def test_report_without_a_batch_says_so(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    out = run(["report"], capsys)
    assert "no batch measurement yet" in out


def test_report_reads_the_saved_summary_not_the_raw_markdown(tmp_path, capsys, monkeypatch) -> None:
    """`show_report` used to `print(doc.read_text())` — every `|` and `**` in
    the markdown table syntax landed on the screen literally. This is the
    seam that replaced it: a small saved summary, rendered for a terminal."""
    import json

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    summary = {
        "generated_at": "2026-09-04 16:58 UTC",
        "cases_count": 300,
        "holdout": 21,
        "total_paise": 12_085_000,
        "agent": {
            "cases": 300, "attempts": 217, "attempts_in_restricted_window": 0,
            "futile_attempts": 0, "messages": 56, "intervention_paise": 9200,
            "held_for_human": 27, "closed_uneconomic": 0,
            "by_class": {}, "by_rung": {}, "by_confidence_band": {},
            "by_source": {"rules-table": 300},
        },
        "base": {
            "cases": 300, "attempts": 900, "attempts_in_restricted_window": 117,
            "futile_attempts": 108, "messages": 0, "intervention_paise": 0,
            "held_for_human": 0, "closed_uneconomic": 0,
            "by_class": {}, "by_rung": {}, "by_confidence_band": {}, "by_source": {},
        },
    }
    (tmp_path / "docs" / "batch-measurement.json").write_text(json.dumps(summary))

    out = run(["report"], capsys)
    assert "217" in out and "900" in out and "683" in out
    assert "|" not in out
    assert "**" not in out
    assert "##" not in out


def test_policy_shows_the_file_it_loaded_from(capsys) -> None:
    """A reader should be able to check a claim against the running system
    without opening config/policy.yaml and parsing it by eye."""
    out = run(["policy"], capsys)
    assert "config/policy.yaml" in out
    assert "NPCI execution bands" in out
    assert "10:00-13:00" in out
    assert "17:00-21:30" in out


def test_policy_reflects_an_override_not_only_the_shipped_file(capsys, tmp_path, monkeypatch) -> None:
    override = tmp_path / "policy.yaml"
    override.write_text(pathlib.Path("config/policy.yaml").read_text().replace("cap: 3", "cap: 1"))
    monkeypatch.setenv("UNHALTED_POLICY", str(override))
    importlib.reload(policy_mod)
    try:
        out = run(["policy"], capsys)
        assert "cap  1 per billing cycle" in out
    finally:
        monkeypatch.delenv("UNHALTED_POLICY", raising=False)
        importlib.reload(policy_mod)


def test_verbose_shows_inputs_that_the_default_hides(db, capsys) -> None:
    path, case_id = db
    plain = run(["--db", path, "case", case_id], capsys)
    verbose = run(["--db", path, "case", case_id, "-v"], capsys)
    assert len(verbose) > len(plain)
    assert "error_reason" in verbose


def test_breakeven_reads_real_stored_cases(db, capsys) -> None:
    """C9c. Computed from what is in the store, not from a batch fixture."""
    path, _ = db
    out = run(["--db", path, "breakeven"], capsys)
    assert "MONEY AT RISK" in out
    assert "BREAKS EVEN AT" in out
    assert "NOT REPORTED" in out


def test_calibration_reads_real_stored_cases(db, capsys) -> None:
    """The db fixture's case is still open, so this is the honest zero."""
    path, _ = db
    out = run(["--db", path, "calibration"], capsys)
    assert "CALIBRATION" in out
    assert "nothing to" in out.lower() or "no case" in out.lower()


def test_calibration_reports_a_real_recovery(tmp_path, capsys) -> None:
    from unhalted.agent import mark_recovered

    path = str(tmp_path / "recovered.db")
    store = Store(path)
    case = handle_failure(store, signal(reason="insufficient_funds"), now=NOW)
    mark_recovered(store, case.id, payment_id="pay_X", amount_paise=49900, now=NOW)
    store.close()

    out = run(["--db", path, "calibration"], capsys)
    assert "auto-execute" in out
    assert "too few to conclude" in out


def test_breakeven_says_so_when_there_is_nothing_to_compute(tmp_path, capsys) -> None:
    from unhalted.store import Store

    path = str(tmp_path / "empty.db")
    Store(path).close()
    assert cli.main(["--db", path, "breakeven"]) == 1
    assert "no diagnosed cases yet" in capsys.readouterr().out


def test_run_due_executes_from_the_command_line(db, capsys) -> None:
    """#31. The same function a scheduler would call over HTTP."""
    path, _ = db
    out = run(["--db", path, "run-due"], capsys)
    assert "claimed=" in out
    assert "worker=" in out


def test_global_flags_work_after_the_subcommand_too(db, capsys) -> None:
    """argparse puts an option on the parser it was declared against, so
    `--db` before the subcommand and `--db` after it are different flags. Both
    positions have to work or the tool teaches a distinction nobody wants."""
    path, case_id = db
    before = run(["--db", path, "case", case_id], capsys)
    after = run(["case", case_id, "--db", path], capsys)
    assert case_id in before
    assert case_id in after


def test_an_absent_flag_on_the_subcommand_does_not_erase_the_global_one(db, capsys) -> None:
    """The argparse trap: a subparser `default=None` silently overwrites what
    the top-level parser already read. SUPPRESS is why this passes."""
    path, case_id = db
    assert case_id in run(["--db", path, "cases"], capsys)


def test_a_stated_time_announces_itself(db, capsys) -> None:
    path, _ = db
    out = run(["--db", path, "run-due", "--at", "2026-09-04 11:00"], capsys)
    assert "CLOCK OVERRIDDEN" in out
    assert "Not for recording" in out


def test_an_unreadable_time_is_refused_with_the_format(db, capsys) -> None:
    path, _ = db
    assert cli.main(["--db", path, "run-due", "--at", "tomorrow-ish"]) == 2
    assert "2026-09-04 11:00" in capsys.readouterr().out


def test_a_database_deleted_under_its_own_log_says_so(tmp_path, capsys) -> None:
    """`rm unhalted.db` leaves -wal and -shm behind and the next open fails
    with a bare `disk I/O error`. Resetting for a demo is exactly when somebody
    deletes a database by hand, so the message has to name the remedy."""
    path = tmp_path / "gone.db"
    (tmp_path / "gone.db-wal").touch()
    (tmp_path / "gone.db-shm").touch()

    assert cli.main(["--db", str(path), "cases"]) == 2
    out = capsys.readouterr().out
    assert "write-ahead log" in out
    assert "rm -f" in out
    assert "Traceback" not in out
