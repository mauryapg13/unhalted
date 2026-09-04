"""A proposed policy change never applies itself, and never trusts a field
or a quote the model invented.
"""

from __future__ import annotations

import json

import pytest

from unhalted.core import policy_change as pc


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
    monkeypatch.setattr(pc.config, "model_api_key", lambda: "test-key")


def test_no_api_key_fails_honestly(monkeypatch) -> None:
    monkeypatch.setattr(pc.config, "model_api_key", lambda: "")
    result = pc.propose("some circular")
    assert result.failed
    assert "no model API key" in result.failure_reason


def test_a_supported_change_with_a_real_quote_is_proposed(monkeypatch) -> None:
    text = "Effective immediately, the maximum frictionless UPI Autopay debit is Rs 20,000."
    body = json.dumps({
        "changes": [{
            "field": "limits.frictionless_upi_rupees",
            "proposed_value": 20000,
            "quote": "the maximum frictionless UPI Autopay debit is Rs 20,000",
            "reasoning": "the circular states a new ceiling",
        }],
        "unclear": [],
    })
    monkeypatch.setattr(pc.httpx, "post", respond(body))

    result = pc.propose(text)

    assert not result.failed
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.field == "limits.frictionless_upi_rupees"
    assert change.proposed_value == 20000
    assert change.current_value == 15000, "read from the live policy, not the model"
    assert result.dropped == ()


def test_an_unknown_field_is_dropped_not_applied(monkeypatch) -> None:
    text = "The nudge message template must now include the merchant's GSTIN."
    body = json.dumps({
        "changes": [{
            "field": "nudge.message_template",
            "proposed_value": "include GSTIN",
            "quote": "must now include the merchant's GSTIN",
            "reasoning": "seems required",
        }],
        "unclear": [],
    })
    monkeypatch.setattr(pc.httpx, "post", respond(body))

    result = pc.propose(text)

    assert result.changes == ()
    assert len(result.dropped) == 1
    assert "nudge.message_template" in result.dropped[0]


def test_a_quote_that_does_not_appear_in_the_text_is_dropped(monkeypatch) -> None:
    text = "The retry cap remains three attempts per cycle, unchanged."
    body = json.dumps({
        "changes": [{
            "field": "retries.cap",
            "proposed_value": 5,
            "quote": "the retry cap is now five attempts",
            "reasoning": "invented",
        }],
        "unclear": [],
    })
    monkeypatch.setattr(pc.httpx, "post", respond(body))

    result = pc.propose(text)

    assert result.changes == ()
    assert len(result.dropped) == 1
    assert "no supporting quote" in result.dropped[0]


def test_unclear_items_pass_through_without_becoming_changes(monkeypatch) -> None:
    text = "Contact windows for high-value customers may need review at a later date."
    body = json.dumps({
        "changes": [],
        "unclear": ["a possible future change to contact hours, with no stated times"],
    })
    monkeypatch.setattr(pc.httpx, "post", respond(body))

    result = pc.propose(text)

    assert result.changes == ()
    assert len(result.unclear) == 1


def test_a_truncated_response_fails_rather_than_guesses(monkeypatch) -> None:
    monkeypatch.setattr(pc.httpx, "post", respond("", finish_reason="length"))
    result = pc.propose("a long circular")
    assert result.failed
    assert "truncated" in result.failure_reason


def test_unparseable_json_fails_honestly(monkeypatch) -> None:
    monkeypatch.setattr(pc.httpx, "post", respond("not json at all"))
    result = pc.propose("a circular")
    assert result.failed
    assert "unparseable JSON" in result.failure_reason


def test_nothing_here_writes_to_the_policy_file(monkeypatch, tmp_path) -> None:
    """The one property that matters most: propose() has no path to a
    filesystem write at all."""
    import inspect
    source = inspect.getsource(pc)
    assert "write_text" not in source
    assert "open(" not in source
