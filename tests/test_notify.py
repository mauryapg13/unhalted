"""`nudge_body` is shared between the customer terminal and the real runner
so the two do not silently drift into two different messages for the same
event, and it carries a pay link when one exists rather than the customer
having nothing to do but wait on a retry that may not work.
"""

from __future__ import annotations

from unhalted.shell.notify import nudge_body


def test_a_plain_nudge_asks_the_customer_to_reply():
    body = nudge_body(499.0)
    assert "Rs 499" in body
    assert "Reply here if that doesn't suit" in body
    assert "Reply STOP" in body


def test_a_pay_link_replaces_the_reply_prompt_not_stacks_beside_it():
    body = nudge_body(499.0, pay_link="https://rzp.io/i/AbC123")
    assert "https://rzp.io/i/AbC123" in body
    assert "Reply here if that doesn't suit" not in body
    assert "Reply STOP" in body, "opting out must survive regardless of a link"


def test_merchant_and_when_are_optional_but_used_if_given():
    body = nudge_body(499.0, merchant="Acme Streaming", when="04 Sep 08:00")
    assert "Acme Streaming" in body
    assert "04 Sep 08:00" in body
