"""The webhook endpoint, end to end.

Every payload here comes from Razorpay's published documentation — see
tests/fixtures/razorpay/PROVENANCE.md. None is written by hand, because a
fixture invented by whoever wrote the parser proves only that the two agree.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from unhalted.ingest import webhooks
from unhalted.models import CaseState, DiagnosisClass
from unhalted.shell.windows import is_execution_allowed
from unhalted.store import Store

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "razorpay"
SECRET = "test_webhook_secret"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    store = Store(str(tmp_path / "t.db"))
    monkeypatch.setattr(webhooks, "get_store", lambda: store)
    with TestClient(webhooks.app) as c:
        c.store = store
        yield c
    store.close()


def post(
    client,
    event: dict,
    *,
    secret: str = SECRET,
    event_id: str | None = "evt_1",
    signature: str | None = None,
):
    body = json.dumps(event).encode()
    headers = {"x-razorpay-signature": signature if signature is not None else sign(body, secret)}
    if event_id:
        headers["x-razorpay-event-id"] = event_id
    return client.post("/webhooks/razorpay", content=body, headers=headers)


# -- the path that has to work ------------------------------------------------


def test_a_failed_payment_becomes_a_case_with_a_scheduled_retry(client) -> None:
    r = post(client, load("payment_failed_netbanking"))
    assert r.status_code == 200, r.text
    case_id = r.json()["case_id"]

    detail = client.get(f"/cases/{case_id}").json()

    assert detail["diagnosis"]["klass"] == DiagnosisClass.RECOVERABLE_TECHNICAL.value
    assert detail["diagnosis"]["source"] == "rules-table"

    kinds = [row["decision_type"] for row in detail["timeline"]]
    assert kinds == ["ingest", "diagnosis", "escalation", "schedule"]

    scheduled = detail["timeline"][-1]
    assert scheduled["action"].startswith("retry at")
    assert scheduled["rule_version"]


def test_the_scheduled_retry_never_lands_in_a_restricted_band(client) -> None:
    case_id = post(client, load("payment_failed_upi")).json()["case_id"]
    scheduled = client.get(f"/cases/{case_id}").json()["timeline"][-1]
    when = datetime.fromisoformat(scheduled["action"].removeprefix("retry at ").replace(" IST", ""))
    assert is_execution_allowed(when).allowed


def test_the_timeline_is_readable_by_case_id(client) -> None:
    case_id = post(client, load("payment_failed_wallets")).json()["case_id"]
    detail = client.get(f"/cases/{case_id}").json()
    assert detail["case"]["id"] == case_id
    assert len(detail["signals"]) == 1
    assert detail["signals"][0]["source"] == "razorpay:payment.failed"


def test_an_unknown_case_id_is_a_404(client) -> None:
    assert client.get("/cases/CASE-NOPE").status_code == 404


# -- the paths that have to fail correctly ------------------------------------


def test_a_forged_signature_is_rejected(client) -> None:
    r = post(client, load("payment_failed_netbanking"), signature="deadbeef")
    assert r.status_code == 401
    assert client.store.all_cases() == []


def test_a_signature_from_the_wrong_secret_is_rejected(client) -> None:
    r = post(client, load("payment_failed_netbanking"), secret="not_the_secret")
    assert r.status_code == 401
    assert client.store.all_cases() == []


def test_a_missing_signature_is_rejected(client) -> None:
    body = json.dumps(load("payment_failed_netbanking")).encode()
    r = client.post("/webhooks/razorpay", content=body)
    assert r.status_code == 401


def test_the_endpoint_refuses_to_run_without_a_configured_secret(client, monkeypatch) -> None:
    """An endpoint accepting unsigned webhooks is one anyone can open cases on."""
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET")
    r = post(client, load("payment_failed_netbanking"))
    assert r.status_code == 503


def test_a_signed_but_malformed_body_is_rejected(client) -> None:
    body = b"{not json"
    r = client.post(
        "/webhooks/razorpay", content=body, headers={"x-razorpay-signature": sign(body)}
    )
    assert r.status_code == 400


def test_a_signed_event_missing_its_payment_entity_is_rejected(client) -> None:
    r = post(client, {"event": "payment.failed", "payload": {}})
    assert r.status_code == 400
    assert client.store.all_cases() == []


def test_an_event_we_do_not_handle_is_acknowledged_not_rejected(client) -> None:
    """Razorpay resends anything we reject, so a 4xx here would loop forever."""
    r = post(client, {"event": "payment.captured", "payload": {}})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert client.store.all_cases() == []


# -- redelivery ---------------------------------------------------------------


def test_a_redelivered_event_does_not_open_a_second_case(client) -> None:
    """Razorpay redelivers. Two cases for one failure would count the money twice."""
    event = load("payment_failed_netbanking")
    first = post(client, event, event_id="evt_dup")
    second = post(client, event, event_id="evt_dup")

    assert second.json()["status"] == "duplicate"
    assert second.json()["case_id"] == first.json()["case_id"]
    assert len(client.store.all_cases()) == 1


def test_the_same_payment_arriving_under_a_new_event_id_reuses_the_case(client) -> None:
    """Belt and braces: even a fresh event id must not double-count a payment."""
    event = load("payment_failed_netbanking")
    first = post(client, event, event_id="evt_a")
    second = post(client, event, event_id="evt_b")

    assert second.json()["case_id"] == first.json()["case_id"]
    assert len(client.store.all_cases()) == 1


def test_a_novel_error_reason_is_held_for_a_human_not_retried(client) -> None:
    event = load("payment_failed_netbanking")
    event["payload"]["payment"]["entity"]["error_reason"] = "DECLINED - AP RULE 7A"
    case_id = post(client, event).json()["case_id"]

    detail = client.get(f"/cases/{case_id}").json()
    assert detail["diagnosis"]["klass"] == DiagnosisClass.UNKNOWN.value
    assert detail["case"]["state"] == CaseState.HELD_FOR_HUMAN.value
    assert "schedule" not in [r["decision_type"] for r in detail["timeline"]]


def test_the_signal_is_durable_before_any_processing_happens(client) -> None:
    """Razorpay retries anything slow, so a webhook that spends seconds in
    diagnosis will be sent again. The event should already be on disk by then
    rather than depending on that retry arriving at all."""
    import unhalted.ingest.webhooks as wh

    seen: dict[str, bool] = {}
    real = wh.handle_failure

    def slow(store, signal, **kwargs):
        # By the time any processing runs, the case must already exist.
        seen["case_existed"] = store.case_for_payment(signal.payment_id) is not None
        return real(store, signal, **kwargs)

    wh.handle_failure = slow
    try:
        r = post(client, load("payment_failed_netbanking"))
        assert r.status_code == 200
        assert seen["case_existed"], "the signal was not persisted before processing"
    finally:
        wh.handle_failure = real


def test_a_payment_in_another_currency_is_refused_at_ingest() -> None:
    """Issue #24. Razorpay accepts USD orders; every rule below here is Indian."""
    import pytest

    from unhalted.ingest.normalize import UnsupportedEvent, from_payment_failed

    def event(currency: str) -> dict:
        return {
            "event": "payment.failed",
            "payload": {"payment": {"entity": {
                "id": "pay_CUR", "amount": 49900, "currency": currency,
                "method": "card", "error_reason": "insufficient_funds",
                "contact": "+919845127634", "created_at": 1788381176,
            }}},
        }

    assert from_payment_failed(event("INR")).amount_rupees == 499.0

    with pytest.raises(UnsupportedEvent, match="USD"):
        from_payment_failed(event("USD"))
