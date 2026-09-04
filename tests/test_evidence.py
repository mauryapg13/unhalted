"""The one check shared by the reply parser and the policy-change proposer:
does a span the model produced actually appear in what it was given.
"""

from __future__ import annotations

from unhalted.core.evidence import quotes_the_source


def test_an_exact_span_quotes() -> None:
    assert quotes_the_source("salary aayega", "salary aayega 5th ko, tab try karna")


def test_spacing_and_case_are_not_meaning() -> None:
    assert quotes_the_source("SALARY   AAYEGA", "salary aayega 5th ko")


def test_empty_or_blank_never_quotes() -> None:
    assert not quotes_the_source("", "anything")
    assert not quotes_the_source("   ", "anything")


def test_a_paraphrase_is_not_a_quote() -> None:
    assert not quotes_the_source(
        "I will pay you next month", "salary aayega 5th ko, tab try karna"
    )
