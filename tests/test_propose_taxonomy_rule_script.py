"""Clustering is plain grouping — no model call needed, and worth testing
directly against the real store rather than only through the script's exit
code.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime

import pytest

from unhalted.agent import handle_failure
from unhalted.models import FailureSignal
from unhalted.shell.windows import IST
from unhalted.store import Store

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "propose_taxonomy_rule.py"
NOW = datetime(2026, 9, 4, 11, 0, tzinfo=IST)


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("propose_taxonomy_rule_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "cluster.db"))
    yield s
    s.close()


def _open(store, reason: str, *, method="card", source="issuer", n=1) -> None:
    for i in range(n):
        signal = FailureSignal(
            payment_id=f"pay_{reason}_{i}", customer_ref=f"cust_{reason}_{i}",
            amount_paise=49900, occurred_at=NOW, source="test",
            method=method, error_reason=reason, error_source=source,
        )
        handle_failure(store, signal, now=NOW)


def test_an_empty_store_has_no_clusters(script, store) -> None:
    assert script.unclassified_clusters(store) == []


def test_a_genuinely_unmatched_reason_is_clustered(script, store) -> None:
    _open(store, "totally_novel_reason", n=3)
    clusters = script.unclassified_clusters(store)
    assert clusters == [(("card", "totally_novel_reason", "issuer", ""), 3)]


def test_a_documented_reason_is_not_clustered_as_unclassified(script, store) -> None:
    """insufficient_funds has a real taxonomy rule — it must never show up
    here even though the case exists."""
    _open(store, "insufficient_funds")
    assert script.unclassified_clusters(store) == []


def test_clusters_are_sorted_by_count_descending(script, store) -> None:
    _open(store, "reason_a", n=1)
    _open(store, "reason_b", n=5)
    clusters = script.unclassified_clusters(store)
    assert [count for _key, count in clusters] == [5, 1]


def test_render_clusters_handles_the_empty_case(script) -> None:
    assert "nothing unclassified" in script.render_clusters([])


def test_render_clusters_shows_the_count_and_the_key(script) -> None:
    out = script.render_clusters([(("card", "some_reason", "issuer", ""), 4)])
    assert "4" in out
    assert "some_reason" in out
