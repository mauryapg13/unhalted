"""Drafting, and what happens when the model invents something.

The specification's scenario is a model writing "pay today and get 20% off next
month" when no such offer exists. The block is half the requirement; the other
half is what happens next.
"""

from __future__ import annotations

import pytest

from unhalted.core import draft


class Model:
    """Returns scripted drafts in order. A test double, not a simulation of
    behaviour — the drafts are the input to the thing under test."""

    def __init__(self, *bodies: str) -> None:
        self.bodies = list(bodies)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        return self.bodies.pop(0) if self.bodies else None


GOOD = "Your Acme payment of Rs 499 did not go through. Reply STOP to opt out."
INVENTED = "Pay today and get 20% off next month! Acme Rs 499. Reply STOP."


def nudge(**kw):
    return draft.draft_nudge(amount_paise=49900, merchant="Acme", when="2026-09-25", **kw)


def test_a_clean_draft_is_sent_as_written(monkeypatch) -> None:
    monkeypatch.setattr(draft, "_call", Model(GOOD))
    body, attempts = nudge()
    assert body == GOOD
    assert len(attempts) == 1 and attempts[0].passed


def test_an_invented_discount_is_blocked_and_regenerated(monkeypatch) -> None:
    """The specification's scenario, end to end."""
    model = Model(INVENTED, GOOD)
    monkeypatch.setattr(draft, "_call", model)

    body, attempts = nudge()

    assert body == GOOD, "the corrected draft should be the one sent"
    assert len(attempts) == 2
    assert not attempts[0].passed and attempts[1].passed
    assert "20% off next month" not in body


def test_the_regeneration_tells_the_model_what_was_wrong(monkeypatch) -> None:
    model = Model(INVENTED, GOOD)
    monkeypatch.setattr(draft, "_call", model)
    nudge()

    assert len(model.prompts) == 2
    correction = model.prompts[1]
    assert "rejected" in correction
    assert "offer" in correction or "percentage" in correction
    assert "Offer nothing" in correction


def test_a_model_that_invents_twice_gets_no_third_chance(monkeypatch) -> None:
    """Asking until it complies is not a safety mechanism."""
    model = Model(INVENTED, INVENTED, GOOD)
    monkeypatch.setattr(draft, "_call", model)

    body, attempts = nudge()

    assert len(attempts) == 2, "only one correction is offered"
    assert body != INVENTED
    assert "20%" not in body


def test_the_fallback_passes_the_lint_by_construction(monkeypatch) -> None:
    """What a customer receives when the model cannot be reached at all.

    Plainer than a drafted message, and complete: amount, merchant, and a way
    to stop.
    """
    monkeypatch.setattr(draft, "_call", Model())
    body, attempts = nudge()

    assert attempts == []
    from unhalted.shell.lint import check

    assert check(body, amount_paise=49900, merchant="Acme").passed
    assert "499" in body and "Acme" in body and "STOP" in body


@pytest.mark.parametrize("bad", [
    "Acme Rs 499. Legal action follows. Reply STOP.",
    "Acme Rs 499. Final warning! Reply STOP.",
    "Acme Rs 499. We guarantee this is the last one. Reply STOP.",
])
def test_every_kind_of_violation_triggers_the_same_correction(bad: str, monkeypatch) -> None:
    monkeypatch.setattr(draft, "_call", Model(bad, GOOD))
    body, attempts = nudge()
    assert not attempts[0].passed
    assert body == GOOD
