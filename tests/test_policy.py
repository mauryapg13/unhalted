"""The policy loader: parses config/policy.yaml, and refuses to guess.

These pin values against the *real* shipped file as well as testing the
parser against constructed ones — a parser that works on hand-built YAML but
silently disagrees with what's actually in config/policy.yaml would be worse
than no test at all.
"""

from __future__ import annotations

from datetime import time, timedelta

import pytest
import yaml

from unhalted import policy


def test_the_real_shipped_file_loads_without_error() -> None:
    p = policy.load()
    assert p.version


def test_the_real_file_matches_what_every_module_used_to_hardcode() -> None:
    """The whole point: centralising must not silently change behaviour."""
    p = policy.load()
    assert p.npci_restricted_bands == (
        (time(10, 0), time(13, 0)),
        (time(17, 0), time(21, 30)),
    )
    assert p.npci_rule_version == "npci-2025-08-01"
    assert (p.contact_open, p.contact_close) == (time(8, 0), time(19, 0))
    assert p.retry_cap == 3
    assert p.backoff_raw["recoverable-technical"] == (
        timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6),
    )
    assert p.backoff_raw["recoverable-balance"] == (
        timedelta(days=1), timedelta(days=1), timedelta(days=2),
    )
    assert p.backoff_raw["notification-gap"] == (timedelta(hours=25),) * 3
    assert p.default_backoff == timedelta(hours=6)
    assert (p.confidence_auto_execute, p.confidence_sampled_qa) == (0.90, 0.70)
    assert (p.reply_acts_on_money, p.reply_protective, p.reply_cancellation) == (
        0.70, 0.50, 0.85,
    )
    assert p.ladder_rung_costs_paise == {
        "silent-retry": 0, "nudge": 100, "reauthorisation": 200,
        "voice-call": 800, "human-callback": 6000,
    }
    assert p.frictionless_upi_paise == 15_000 * 100
    assert p.frictionless_upi_bfsi_paise == 100_000 * 100
    assert p.upi_mandate_max_paise == 100_000 * 100
    assert p.card_recurring_max_paise == 15_000 * 100
    assert p.emandate_max_paise == 100_00_000 * 100


def minimal() -> dict:
    """The smallest document `load()` will accept, as a base to mutate."""
    return {
        "version": "test",
        "npci": {"restricted_bands": [["10:00", "13:00"]], "rule_version": "v"},
        "contact": {"open": "08:00", "close": "19:00"},
        "retries": {
            "cap": 3,
            "backoff": {"recoverable-technical": ["30m"]},
            "default_backoff": "6h",
        },
        "confidence": {"auto_execute": 0.9, "auto_execute_sampled_qa": 0.7},
        "reply_policy": {
            "acts_on_money": 0.7, "protective": 0.5, "cancellation": 0.85,
            "rule_version": "v",
        },
        "ladder": {"rule_version": "v", "rung_costs_rupees": {"nudge": 1}},
        "limits": {
            "rule_version": "v", "frictionless_upi_rupees": 1,
            "frictionless_upi_bfsi_rupees": 1, "upi_mandate_max_rupees": 1,
            "card_recurring_max_rupees": 1, "emandate_max_rupees": 1,
        },
    }


def write(tmp_path, doc: dict):
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


def test_a_missing_file_is_refused_not_defaulted(tmp_path) -> None:
    with pytest.raises(policy.BadPolicy, match="no policy file"):
        policy.load(tmp_path / "nope.yaml")


def test_a_document_that_is_not_a_mapping_is_refused(tmp_path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text("- just\n- a\n- list\n")
    with pytest.raises(policy.BadPolicy, match="did not parse to a mapping"):
        policy.load(p)


def test_a_missing_required_key_is_refused_not_defaulted(tmp_path) -> None:
    doc = minimal()
    del doc["retries"]["cap"]
    with pytest.raises(policy.BadPolicy, match="retries.cap"):
        policy.load(write(tmp_path, doc))


def test_an_unparseable_time_is_refused(tmp_path) -> None:
    doc = minimal()
    doc["contact"]["open"] = "8am"
    with pytest.raises(policy.BadPolicy, match="not a time"):
        policy.load(write(tmp_path, doc))


def test_an_unparseable_duration_is_refused(tmp_path) -> None:
    doc = minimal()
    doc["retries"]["default_backoff"] = "soon"
    with pytest.raises(policy.BadPolicy, match="not a duration"):
        policy.load(write(tmp_path, doc))


def test_a_duration_with_no_unit_letter_is_refused(tmp_path) -> None:
    doc = minimal()
    doc["retries"]["default_backoff"] = "30"
    with pytest.raises(policy.BadPolicy, match="not a duration"):
        policy.load(write(tmp_path, doc))


def test_days_hours_and_minutes_all_parse(tmp_path) -> None:
    doc = minimal()
    doc["retries"]["backoff"]["recoverable-technical"] = ["45m", "3h", "2d"]
    p = policy.load(write(tmp_path, doc))
    assert p.backoff_raw["recoverable-technical"] == (
        timedelta(minutes=45), timedelta(hours=3), timedelta(days=2),
    )


def test_rupees_convert_to_paise_exactly(tmp_path) -> None:
    doc = minimal()
    doc["limits"]["frictionless_upi_rupees"] = 15_000
    p = policy.load(write(tmp_path, doc))
    assert p.frictionless_upi_paise == 1_500_000
