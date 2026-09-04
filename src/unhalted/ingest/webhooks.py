"""The webhook endpoint. Where real failures enter the system.

Two rules from Razorpay's own webhook documentation are load-bearing here and
are easy to get subtly wrong:

1. The signature is HMAC-SHA256 over the **raw request body**. Parsing the JSON
   and re-serialising it before hashing produces a different byte string and a
   signature that never matches — so the raw bytes are read before anything
   touches them.
2. Redelivery is expected, not exceptional. `x-razorpay-event-id` is unique per
   event and is how a repeat is recognised. Without it a redelivered failure
   would open a second case and the same rupees would be counted twice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from unhalted import config
from unhalted.agent import handle_failure, mark_recovered
from unhalted.ingest.normalize import UnsupportedEvent, from_payment_failed
from unhalted.models import AuditRecord, Case
from unhalted.runner import run_due
from unhalted.shell import windows
from unhalted.store import Store

log = logging.getLogger("unhalted.ingest")

app = FastAPI(title="unhalted", description="Mandate recovery agent")

#: Events this endpoint acts on. Anything else is acknowledged and ignored —
#: Razorpay will resend anything we reject, so a 4xx on an event we simply do
#: not handle would produce a redelivery loop.
HANDLED_EVENTS = {"payment.failed", "payment_link.paid"}


def get_store() -> Store:
    """Overridden in tests. Kept as a function so the DB path is read at call
    time rather than import time."""
    return Store()


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """Constant-time comparison of the HMAC-SHA256 digest of the raw body."""
    if not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> dict[str, Any]:
    raw = await request.body()

    secret = config.webhook_secret()
    if not secret:
        # Failing closed: an endpoint that accepts unsigned webhooks is an
        # endpoint anyone can open cases on.
        log.error("RAZORPAY_WEBHOOK_SECRET is not set; refusing to accept webhooks")
        raise HTTPException(status_code=503, detail="webhook secret not configured")

    signature = request.headers.get("x-razorpay-signature")
    if not verify_signature(raw, signature, secret):
        log.warning("rejected webhook with invalid signature")
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="body is not valid JSON") from None

    name = event.get("event") if isinstance(event, dict) else None
    if name not in HANDLED_EVENTS:
        return {"status": "ignored", "event": name}

    store = get_store()
    event_id = request.headers.get("x-razorpay-event-id")

    if event_id:
        seen = store.event_seen(event_id)
        if seen:
            log.info("event %s already processed as %s", event_id, seen)
            return {"status": "duplicate", "case_id": seen}

    if name == "payment_link.paid":
        return _handle_payment_link_paid(store, event, event_id)

    try:
        signal = from_payment_failed(event)
    except UnsupportedEvent as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # The signal is made durable before any work is done on it. Razorpay expects
    # a prompt 2xx and retries anything slow, so a webhook that spends seconds
    # in diagnosis is one they will send again — and if this process dies
    # mid-diagnosis, the event should already be on disk rather than depending
    # on that retry arriving.
    case = store.open_case(signal)
    if event_id:
        store.mark_event(event_id, case.id, datetime.now(tz=windows.IST))

    case = handle_failure(store, signal)

    return {"status": "accepted", "case_id": case.id, "state": case.state.value}


def _handle_payment_link_paid(
    store: Store, event: dict[str, Any], event_id: str | None,
) -> dict[str, Any]:
    """The other half of the loop `shell/paylink.py` opens.

    `reference_id` is the case id, set when the link was created
    (`create_payment_link(..., reference_id=case.id)`) and echoed back
    verbatim on this event — verified against `webhooks/payment-links.md`,
    not assumed. A link paid for a case this store does not know about, or
    one with no reference_id at all (an older link, or one created outside
    this flow), is acknowledged and ignored rather than raised: Razorpay
    considers the webhook delivered either way, and there is nothing here to
    act on.
    """
    entity = (
        event.get("payload", {}).get("payment_link", {}).get("entity", {})
    )
    reference_id = entity.get("reference_id")
    payment_entity = event.get("payload", {}).get("payment", {}).get("entity", {})
    payment_id = payment_entity.get("id", "")
    amount_paise = payment_entity.get("amount") or entity.get("amount_paid") or 0

    if not reference_id:
        log.info("payment_link.paid with no reference_id; nothing to close")
        return {"status": "ignored", "event": "payment_link.paid", "reason": "no reference_id"}

    case = store.get_case(reference_id)
    if case is None:
        log.info("payment_link.paid for unknown case %r", reference_id)
        return {"status": "ignored", "event": "payment_link.paid", "reason": "unknown case"}

    if event_id:
        store.mark_event(event_id, case.id, datetime.now(tz=windows.IST))

    mark_recovered(store, case.id, payment_id=payment_id, amount_paise=int(amount_paise))
    return {"status": "recovered", "case_id": case.id}


@app.post("/internal/run-due")
def run_due_endpoint() -> dict[str, object]:
    """Execute whatever has come due. Intended for an external scheduler.

    The same function the CLI calls, reachable over HTTP so that a deployment
    without a long-running process can still have a clock: a cloud scheduler,
    a cron container, or a platform timer posts here and the work happens.

    Idempotent, so a scheduler that retries on a timeout cannot double-charge —
    the second call finds nothing due. Not authenticated, and it must not be
    exposed publicly as it stands; that is a deployment concern this repository
    does not reach, and saying so is better than a token nobody rotates.
    """
    store = get_store()
    report = run_due(store)
    log.info("run-due: %s", report.render().splitlines()[0])
    return {
        "at": report.at.isoformat(),
        "worker": report.worker,
        "claimed": report.claimed,
        "done": report.done,
        "held": report.held,
        "cancelled": report.cancelled,
        "no_adapter": report.no_adapter,
        "failed": report.failed,
        "reclaimed": report.reclaimed,
    }


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    """The case timeline: every signal in, every decision out, in order."""
    store = get_store()
    case: Case | None = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no such case: {case_id}")

    diagnosis = store.latest_diagnosis(case_id)
    timeline: list[AuditRecord] = store.timeline(case_id)

    return {
        "case": case.model_dump(mode="json"),
        "diagnosis": diagnosis.model_dump(mode="json") if diagnosis else None,
        "signals": [s.model_dump(mode="json") for s in store.signals(case_id)],
        "timeline": [r.model_dump(mode="json") for r in timeline],
    }
