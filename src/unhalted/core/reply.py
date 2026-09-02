"""Reading customer replies. The one place a model is genuinely irreplaceable.

Hinglish has no fixed spelling — *nahi*, *nahin*, *nai*, *nhi* are the same word
and nobody agrees which is right. It code-switches mid-sentence. And the intent
is usually implied rather than stated: *"salary aane do"* means "let the salary
come", and nowhere does it say "I will pay". No lookup table survives that.

It also matters more than it looks. For the largest failure class — an empty
account — whether a retry works depends on *when the customer will have money*,
and no API anywhere reports that. The reply is the only source of the fact that
decides recovery.

What this module does not do
----------------------------
It does not decide anything. It returns what it read, with the words that
justify each reading, and the shell decides what follows. Intent precedence —
that a dispute outranks a promise — is policy and lives in `shell/replies.py`
where it can be tested, not in a prompt where it can drift.
"""

from __future__ import annotations

import hashlib
import json
import logging

import httpx

from unhalted import config
from unhalted.models import DetectedIntent, Intent, ParsedReply, Sentiment

log = logging.getLogger("unhalted.core.reply")

#: The endpoint intermittently returns a 200 with null content — OpenRouter
#: routes one model across providers and they do not all behave alike. Observed
#: once in seven calls during evaluation. Retried, and a persistent empty
#: response degrades to a failed parse rather than an exception.
ATTEMPTS = 3
TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = """You read customer replies to a failed subscription payment and return structure.

Reply with ONLY a JSON object. No prose, no markdown fences, no explanation.

{
  "language": "hinglish" | "english" | "hindi" | other,
  "intents": [
    {"type": <one of the types below>, "confidence": 0.0-1.0, "evidence": "<exact words from the reply>"}
  ],
  "payment_date_raw": "<a date the customer named, as YYYY-MM-DD, or null>",
  "condition": "<anything the customer made payment conditional on, or null>",
  "sentiment": "cooperative" | "neutral" | "frustrated" | "distress"
}

Intent types, and nothing outside this list:
  promise-to-pay        they say they will pay, even implicitly
  dispute               they say a charge was wrong, duplicated, or already paid
  set-off-request       they want something adjusted or refunded before paying
  opt-out               they want no further messages
  cancellation-request  they explicitly want the subscription cancelled
  service-complaint     they are unhappy with the service, but not cancelling
  distress              they describe hardship: lost work, illness, no money at all
  channel-preference    they state how they want to be contacted
  unknown               you cannot tell

Rules:
- "evidence" must quote words that actually appear in the reply. Do not paraphrase.
- Anger about the service is service-complaint, NOT cancellation-request. People
  complain without wanting to leave.
- A reply can carry several intents. Report all of them.
- If you are unsure, say so with a low confidence rather than guessing high."""


def prompt_hash() -> str:
    """Identifies the prompt that produced a parse, for replay."""
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]


def _extract(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(text.strip())


def _call_model(reply: str, context: str) -> tuple[str | None, str | None, int]:
    """Returns (content, failure_reason, attempts_made)."""
    key = config.model_api_key()
    if not key:
        return None, "no model API key configured", 0

    user = f"{context}\n\nCustomer reply:\n{reply}" if context else reply
    body = {
        "model": config.model_name(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": 1200,
        "temperature": 0,
    }

    last = "unknown"
    for attempt in range(ATTEMPTS):
        try:
            r = httpx.post(
                f"{config.model_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            last = f"transport error: {e}"
            log.warning("reply parse attempt %d failed: %s", attempt + 1, e)
            continue

        if r.status_code != 200:
            last = f"HTTP {r.status_code}"
            log.warning("reply parse attempt %d: %s", attempt + 1, last)
            continue

        content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content")
        if content and content.strip():
            return content, None, attempt + 1
        last = "model returned empty content"
        log.warning("reply parse attempt %d: empty content", attempt + 1)

    return None, last, ATTEMPTS


def parse(reply: str, *, context: str = "") -> ParsedReply:
    """Read a reply into structure, or record honestly that we could not.

    A failure here is not an exception. The specification requires that replies
    queue with no data loss when the model is unreachable, and that no action
    fires on a stale assumption — so an unparseable reply comes back marked
    `failed`, with the raw text intact, and the shell routes it to a human.
    """
    base = ParsedReply(
        raw=reply,
        model_name=config.model_name() or None,
        prompt_hash=prompt_hash(),
    )

    content, failure, attempts = _call_model(reply, context)
    if content is None:
        return base.model_copy(
            update={"failed": True, "failure_reason": failure, "attempts": attempts}
        )

    try:
        data = _extract(content)
    except (json.JSONDecodeError, ValueError) as e:
        return base.model_copy(
            update={
                "failed": True,
                "failure_reason": f"model returned unparseable JSON: {e}",
                "attempts": attempts,
            }
        )

    intents: list[DetectedIntent] = []
    for raw_intent in data.get("intents") or []:
        try:
            intents.append(
                DetectedIntent(
                    type=Intent(raw_intent["type"]),
                    confidence=float(raw_intent.get("confidence", 0.0)),
                    evidence=str(raw_intent.get("evidence") or ""),
                )
            )
        except (KeyError, ValueError, TypeError):
            # An intent outside the closed set is dropped rather than coerced.
            # Coercing would let the model invent a consequence-free intent.
            log.warning("dropping unrecognised intent: %r", raw_intent)

    try:
        sentiment = Sentiment(data.get("sentiment", "neutral"))
    except ValueError:
        sentiment = Sentiment.NEUTRAL

    return base.model_copy(
        update={
            "language": str(data.get("language") or "unknown"),
            "intents": intents,
            "payment_date_raw": data.get("payment_date_raw") or None,
            "condition": data.get("condition") or None,
            "sentiment": sentiment,
            "attempts": attempts,
        }
    )
