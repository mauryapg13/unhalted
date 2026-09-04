"""A real, payable link — or an honest absence of one.

Verified against Razorpay's own documentation (api/payments/payment-links/
create-standard.md): POST /v1/payment_links, Basic Auth on key_id:key_secret,
the customer-facing URL comes back as `short_url`. Every test here mocks
httpx.post — this must never make a real request during the suite.
"""

from __future__ import annotations

from unittest.mock import patch

from unhalted.shell import paylink


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_no_key_configured_makes_no_network_call(monkeypatch):
    monkeypatch.setattr(paylink.config, "razorpay_key_id", lambda: "")
    monkeypatch.setattr(paylink.config, "razorpay_key_secret", lambda: "secret")
    with patch("httpx.post") as mocked:
        result = paylink.create_payment_link(amount_paise=49900, description="test")
    mocked.assert_not_called()
    assert result is None


def test_a_successful_response_returns_the_payable_url(monkeypatch):
    monkeypatch.setattr(paylink.config, "razorpay_key_id", lambda: "rzp_test_x")
    monkeypatch.setattr(paylink.config, "razorpay_key_secret", lambda: "s3cr3t")
    with patch(
        "httpx.post",
        return_value=FakeResponse(
            200, {"id": "plink_ABC123", "short_url": "https://rzp.io/i/AbC123",
                  "status": "created"},
        ),
    ) as mocked:
        result = paylink.create_payment_link(amount_paise=49900, description="test")

    assert result is not None
    assert result.url == "https://rzp.io/i/AbC123"
    assert result.id == "plink_ABC123"
    assert result.status == "created"

    _, kwargs = mocked.call_args
    assert kwargs["auth"] == ("rzp_test_x", "s3cr3t")
    assert kwargs["json"]["amount"] == 49900
    assert kwargs["json"]["currency"] == "INR"
    assert kwargs["json"]["notify"] == {"sms": False, "email": False}, (
        "we deliver the message ourselves; Razorpay notifying too would be a "
        "second, unrequested message"
    )


def test_a_refusal_returns_none_rather_than_raising(monkeypatch):
    monkeypatch.setattr(paylink.config, "razorpay_key_id", lambda: "id")
    monkeypatch.setattr(paylink.config, "razorpay_key_secret", lambda: "secret")
    with patch("httpx.post", return_value=FakeResponse(400, {}, text="bad request")):
        result = paylink.create_payment_link(amount_paise=49900, description="test")
    assert result is None


def test_a_transport_error_returns_none_rather_than_raising(monkeypatch):
    import httpx

    monkeypatch.setattr(paylink.config, "razorpay_key_id", lambda: "id")
    monkeypatch.setattr(paylink.config, "razorpay_key_secret", lambda: "secret")

    def raises(*a, **k):
        raise httpx.ConnectError("no route to host")

    with patch("httpx.post", side_effect=raises):
        result = paylink.create_payment_link(amount_paise=49900, description="test")
    assert result is None


def test_a_response_with_no_short_url_returns_none(monkeypatch):
    """Defensive: a 200 that somehow doesn't carry the one field we need."""
    monkeypatch.setattr(paylink.config, "razorpay_key_id", lambda: "id")
    monkeypatch.setattr(paylink.config, "razorpay_key_secret", lambda: "secret")
    with patch("httpx.post", return_value=FakeResponse(200, {"id": "plink_X"})):
        result = paylink.create_payment_link(amount_paise=49900, description="test")
    assert result is None
