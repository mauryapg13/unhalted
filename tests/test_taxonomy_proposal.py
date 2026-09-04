"""A proposed taxonomy rule never applies itself, and never trusts a class
or a quote the model invented — the same discipline the policy-change
proposer already applies, asked of a different kind of source text.
"""

from __future__ import annotations

import json

import pytest

from unhalted.core import taxonomy_proposal as tp


class FakeResponse:
    status_code = 200

    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body


def respond(content: str | None, *, finish_reason: str = "stop", cost: float = 0.0002):
    def fake_post(*args, **kwargs):
        return FakeResponse({
            "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
            "usage": {"cost": cost},
        })
    return fake_post


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    monkeypatch.setattr(tp.config, "model_api_key", lambda: "test-key")


def test_no_api_key_fails_honestly(monkeypatch) -> None:
    monkeypatch.setattr(tp.config, "model_api_key", lambda: "")
    result = tp.propose(method="card", error_reason="x", doc_text="anything")
    assert result.failed
    assert "no model API key" in result.failure_reason


def test_a_grounded_proposal_is_returned(monkeypatch) -> None:
    doc = "insufficient_fund | The account did not have enough funds to complete the transaction."
    body = json.dumps({
        "proposable": True,
        "klass": "recoverable-balance",
        "directness": "direct",
        "quote": "The account did not have enough funds to complete the transaction",
        "rationale": "Razorpay states the cause directly.",
    })
    monkeypatch.setattr(tp.httpx, "post", respond(body))

    result = tp.propose(method="card", error_reason="insufficient_fund", doc_text=doc)

    assert not result.failed
    assert result.proposable
    from unhalted.models import DiagnosisClass
    assert result.klass is DiagnosisClass.RECOVERABLE_BALANCE
    assert result.directness == 1.0


def test_a_quote_that_does_not_appear_in_the_text_is_refused(monkeypatch) -> None:
    doc = "some_reason | A generic decline with no further detail given."
    body = json.dumps({
        "proposable": True,
        "klass": "recoverable-technical",
        "directness": "direct",
        "quote": "this exact phrase is not actually in the text",
        "rationale": "invented",
    })
    monkeypatch.setattr(tp.httpx, "post", respond(body))

    result = tp.propose(method="card", error_reason="some_reason", doc_text=doc)

    assert result.failed
    assert "no supporting quote" in result.failure_reason


def test_an_unknown_class_is_refused(monkeypatch) -> None:
    doc = "some_reason | states a cause"
    body = json.dumps({
        "proposable": True,
        "klass": "made-up-class",
        "directness": "direct",
        "quote": "states a cause",
        "rationale": "x",
    })
    monkeypatch.setattr(tp.httpx, "post", respond(body))

    result = tp.propose(method="card", error_reason="some_reason", doc_text=doc)

    assert result.failed
    assert "unknown class" in result.failure_reason


def test_the_model_saying_not_proposable_is_honoured_not_overridden(monkeypatch) -> None:
    doc = "this document never mentions the reason in question at all."
    body = json.dumps({
        "proposable": False,
        "klass": None,
        "directness": None,
        "quote": "",
        "rationale": "the text does not mention this reason",
    })
    monkeypatch.setattr(tp.httpx, "post", respond(body))

    result = tp.propose(method="card", error_reason="never_mentioned", doc_text=doc)

    assert not result.failed
    assert not result.proposable
    assert result.klass is None
    assert "does not mention" in result.rationale


def test_a_truncated_response_fails_rather_than_guesses(monkeypatch) -> None:
    monkeypatch.setattr(tp.httpx, "post", respond("", finish_reason="length"))
    result = tp.propose(method="card", error_reason="x", doc_text="some text")
    assert result.failed
    assert "truncated" in result.failure_reason


def test_unparseable_json_fails_honestly(monkeypatch) -> None:
    monkeypatch.setattr(tp.httpx, "post", respond("not json"))
    result = tp.propose(method="card", error_reason="x", doc_text="some text")
    assert result.failed
    assert "unparseable JSON" in result.failure_reason


def test_nothing_here_writes_to_the_taxonomy_file() -> None:
    import inspect
    source = inspect.getsource(tp)
    assert "write_text" not in source
    assert "TABLE[" not in source
    assert "TABLE.update" not in source
