"""Tests that hit the real Razorpay API.

Everything else in this suite runs offline, which means it verifies that our
code agrees with our understanding of Razorpay — not that our understanding is
correct. These tests close that gap: they assert the field names and shapes the
pipeline actually depends on, against the live test-mode API, and fail if
Razorpay changes them.

Excluded by default because they need credentials and a network. Run them with:

    uv run pytest -m live

They create real test-mode objects. No real money is involved, and the suite
refuses to run against anything but an `rzp_test_` key.
"""

from __future__ import annotations

import os
import pathlib

import pytest
import razorpay

pytestmark = pytest.mark.live


def _load_env() -> None:
    env = pathlib.Path(__file__).parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


@pytest.fixture(scope="module")
def client() -> razorpay.Client:
    _load_env()
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key_id or not secret:
        pytest.skip("no Razorpay credentials in the environment")
    if not key_id.startswith("rzp_test_"):
        pytest.fail(f"refusing to run against a non-test key ({key_id[:9]}...)")
    return razorpay.Client(auth=(key_id, secret))


def test_credentials_are_accepted(client: razorpay.Client) -> None:
    client.order.all({"count": 1})


def test_an_order_has_the_fields_the_pipeline_reads(client: razorpay.Client) -> None:
    """`FailureSignal` joins on order_id and reads amount and currency."""
    order = client.order.create(
        {
            "amount": 49900,
            "currency": "INR",
            "notes": {"purpose": "live API contract test"},
        }
    )
    for field in ("id", "amount", "currency", "status", "created_at"):
        assert field in order, f"order is missing {field}"
    assert order["id"].startswith("order_")
    assert isinstance(order["amount"], int), "amount must be paise as an integer"

    fetched = client.order.fetch(order["id"])
    assert fetched["id"] == order["id"]


def test_the_payment_entity_carries_the_error_fields_we_diagnose_on(
    client: razorpay.Client,
) -> None:
    """The three fields the whole taxonomy is keyed on.

    Skips rather than passes vacuously when the account has no failed payment —
    an assertion that silently tests nothing is worse than an absent one.
    """
    payments = client.payment.all({"count": 100})
    failed = [p for p in payments.get("items", []) if p.get("status") == "failed"]
    if not failed:
        pytest.skip("no failed payments on this account yet; captured at C4")

    for payment in failed:
        for field in ("error_code", "error_description", "error_source", "error_step",
                      "error_reason"):
            assert field in payment, f"{payment['id']} is missing {field}"


def test_a_payment_can_be_fetched_by_id(client: razorpay.Client) -> None:
    """The false-failure verification path depends on this."""
    payments = client.payment.all({"count": 1})
    if not payments.get("items"):
        pytest.skip("no payments on this account yet")
    pid = payments["items"][0]["id"]
    assert client.payment.fetch(pid)["id"] == pid


def test_subscriptions_api_is_entitled(client: razorpay.Client) -> None:
    """Pins a fact that changed underneath us mid-build.

    On 2026-08-31 this API returned 401 on every call, including with a freshly
    regenerated key, and the project was designed around its absence. Later the
    same day it began answering — enabling Card and eMandate in the dashboard's
    Subscriptions settings appears to have provisioned it asynchronously.

    Nothing in the pipeline depends on it yet: the shell owns debit timing, and
    under Subscriptions Razorpay owns it instead (T+1, T+2, T+3), which would
    make the NPCI window rules inert. But `subscription.pending` is a second
    real signal source the ingest layer has a seam for, and if this API
    disappears again we want to be told rather than discover it in a demo.
    """
    assert "items" in client.plan.all({"count": 1})
    assert "items" in client.subscription.all({"count": 1})
