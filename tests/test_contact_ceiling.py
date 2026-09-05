"""The contact ceiling: a bound on messages, not on debits.

The retry cap is per case and counts debit attempts. It is not a bound on how
often a person hears from this system, and nothing else was. A customer with
four failing subscriptions consumed no retries at all — the balance flow asks
before it retries — and still received four messages in four days, each one
individually correct: right diagnosis, inside contact hours, one per case.
Nothing looked at the person.

The README listed this ceiling among the hard rules and `notify.py`'s own
docstring said it sat above delivery. Neither was true; there was no counting
anywhere. These tests are what makes the claim real.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise

import pytest

from unhalted.agent import handle_failure, mark_recovered
from unhalted.models import FailureSignal
from unhalted.runner import run_due
from unhalted.shell import windows
from unhalted.store import Store

IST = windows.IST
MONDAY = datetime(2026, 9, 7, 9, 0, tzinfo=IST)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "ceiling.db"))
    yield s
    s.close()


def signal(n: int, customer: str = "cust_busy", *, at: datetime) -> FailureSignal:
    return FailureSignal(
        payment_id=f"pay_CEIL{n}",
        customer_ref=customer,
        amount_paise=49900,
        method="card",
        error_reason="insufficient_fund",
        error_source="customer",
        occurred_at=at,
        source="test",
    )


def delivered(store: Store, customer: str = "cust_busy") -> list[datetime]:
    return store.contacts_since(customer, MONDAY - timedelta(days=30))


# -- the rule itself ----------------------------------------------------------


def test_the_first_message_of_the_week_is_permitted() -> None:
    check = windows.contact_budget([], now=MONDAY)
    assert check.allowed
    assert check.used == 0


def test_a_second_message_inside_the_week_is_refused() -> None:
    check = windows.contact_budget([MONDAY], now=MONDAY + timedelta(days=2))
    assert not check.allowed
    assert "contact ceiling reached" in check.reason


def test_a_message_older_than_the_window_does_not_count() -> None:
    """The window rolls; it is not a calendar period that resets on a Monday.

    Read from policy rather than hard-coded: the window was seven days before
    it was fourteen, and a test asserting the old number would have gone green
    against the wrong rule.
    """
    aged_out = MONDAY + windows.POLICY.contact_window + timedelta(days=1)
    check = windows.contact_budget([MONDAY], now=aged_out)
    assert check.allowed, "a message older than the window has aged out"


def test_a_refused_message_is_told_when_the_budget_frees() -> None:
    check = windows.contact_budget([MONDAY], now=MONDAY + timedelta(days=1))
    assert check.free_at is not None
    assert check.free_at.date() == (MONDAY + windows.POLICY.contact_window).date()


# -- through the pipeline -----------------------------------------------------


def test_four_failing_subscriptions_produce_one_message_not_four(store: Store) -> None:
    """The probe that found this, as a test.

    Four separate cases for one person, four consecutive days, zero retries
    consumed by any of them.
    """
    for i in range(4):
        at = MONDAY + timedelta(days=i)
        handle_failure(store, signal(i, at=at), now=at)
        run_due(store, now=at)

    assert len(store.all_cases()) == 4, "every failure still opens its own case"
    assert len(delivered(store)) == 1


def test_the_messages_over_budget_are_deferred_not_dropped(store: Store) -> None:
    """Dropping would lose a customer's only notice that a subscription is
    lapsing. A late message is a worse outcome than a prompt one; no message
    is a worse outcome than both."""
    for i in range(4):
        at = MONDAY + timedelta(days=i)
        handle_failure(store, signal(i, at=at), now=at)
        run_due(store, now=at)

    waiting = [a for a in store.pending_actions() if a["kind"] == "nudge"]
    assert len(waiting) == 3
    for action in waiting:
        assert (datetime.fromisoformat(action["scheduled_for"])
                >= MONDAY + windows.POLICY.contact_window)


def test_a_backlog_drains_one_window_at_a_time(store: Store) -> None:
    window = windows.POLICY.contact_window
    for i in range(4):
        at = MONDAY + timedelta(days=i)
        handle_failure(store, signal(i, at=at), now=at)
        run_due(store, now=at)

    for n in range(1, 4):
        run_due(store, now=MONDAY + window * n)

    sent = delivered(store)
    assert len(sent) == 4, "every message eventually goes out"
    for earlier, later in pairwise(sent):
        assert later - earlier >= window, "never two inside one window"


def test_a_payment_confirmation_does_not_spend_the_budget(store: Store) -> None:
    """A message saying "thank you, this is settled" ends a conversation
    rather than pressing one. Counting it would mean the customer who paid
    gets silence the next time something genuinely fails."""
    case = handle_failure(store, signal(9, "cust_paid", at=MONDAY), now=MONDAY)
    run_due(store, now=MONDAY)
    before = len(delivered(store, "cust_paid"))

    mark_recovered(store, case.id, payment_id="pay_OK", amount_paise=49900, now=MONDAY)

    assert len(delivered(store, "cust_paid")) == before


def test_the_ceiling_counts_the_person_not_the_case(store: Store) -> None:
    """Two customers failing on the same day both get their message."""
    for i, who in enumerate(("cust_a", "cust_b")):
        handle_failure(store, signal(i, who, at=MONDAY), now=MONDAY)
    run_due(store, now=MONDAY)

    assert len(delivered(store, "cust_a")) == 1
    assert len(delivered(store, "cust_b")) == 1
