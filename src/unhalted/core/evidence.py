"""Whether a span the model produced actually appears in what it was given.

One check, used everywhere the model is asked to quote something rather than
invent it: a customer's reply, or — for policy changes — the regulatory text
a proposed number is supposed to come from. A model that cites a phrase
nobody wrote is not citing evidence, whatever it calls the field.
"""

from __future__ import annotations


def quotes_the_source(span: str, source: str) -> bool:
    """Does `span` actually appear in `source`?

    Whitespace and case are normalised because models re-space and
    re-capitalise a quote without changing which words it contains. Anything
    beyond that is a paraphrase or an invention, and neither is evidence.
    """
    if not span.strip():
        return False
    squash = " ".join(span.split()).casefold()
    return squash in " ".join(source.split()).casefold()
