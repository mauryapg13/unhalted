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
from unhalted.agent import handle_failure
from unhalted.ingest.normalize import UnsupportedEvent, from_payment_failed
from unhalted.models import AuditRecord, Case
from unhalted.shell import windows
from unhalted.store import Store

log = logging.getLogger("unhalted.ingest")

app = FastAPI(title="unhalted", description="Mandate recovery agent")

#: Events this endpoint acts on. Anything else is acknowledged and ignored —
#: Razorpay will resend anything we reject, so a 4xx on an event we simply do
#: not handle would produce a redelivery loop.
HANDLED_EVENTS = {"payment.failed"}


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

    try:
        signal = from_payment_failed(event)
    except UnsupportedEvent as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    case = handle_failure(store, signal)

    if event_id:
        store.mark_event(event_id, case.id, datetime.now(tz=windows.IST))

    return {"status": "accepted", "case_id": case.id, "state": case.state.value}


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
