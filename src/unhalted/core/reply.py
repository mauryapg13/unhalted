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

import dataclasses
import hashlib
import json
import logging

import httpx

from unhalted import config
from unhalted.models import DetectedIntent, Intent, ParsedReply, Sentiment

log = logging.getLogger("unhalted.core.reply")

#: A genuinely empty 200 is retried; the same input can route to a different
#: provider and come back fine. What is *not* retried is a response cut off at
#: the token ceiling — see `MAX_TOKENS`.
ATTEMPTS = 3

#: Read timeout per network operation, plus a whole-call ceiling. httpx's plain
#: `timeout=` is per-operation, so a slow stream can hold a worker far longer
#: than the number suggests: one call during testing took 611 seconds under a
#: nominal 45.
TIMEOUT_SECONDS = 30
TOTAL_TIMEOUT_SECONDS = 90

#: This model reasons before it answers, and spends the completion budget doing
#: it. At 1200 a reply carrying several intents parses one time in three: the
#: budget runs out mid-thought and `content` comes back truncated or empty with
#: `finish_reason: "length"`. Measured over 90 live calls, single-intent replies
#: went 97% -> 100% and multi-intent 33% -> 93% on raising this to 4000, at
#: *half* the cost per successful parse — a truncated call bills for every token
#: it burned and returns nothing usable.
MAX_TOKENS = 4000

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


@dataclasses.dataclass(frozen=True)
class ModelCall:
    """What one or more attempts at the endpoint produced."""

    content: str | None
    failure_reason: str | None
    attempts: int
    #: From OpenRouter's own `usage.cost`, summed over every attempt made —
    #: including the ones that returned nothing, because those are billed too.
    cost_usd: float = 0.0
    truncated: bool = False


def _quotes_the_reply(evidence: str, reply: str) -> bool:
    """Does this span actually appear in what the customer wrote?

    Whitespace and case are normalised because models re-space and re-capitalise
    a quote without changing which words it contains. Anything beyond that is a
    paraphrase or an invention, and neither is evidence.
    """
    if not evidence.strip():
        return False
    squash = " ".join(evidence.split()).casefold()
    return squash in " ".join(reply.split()).casefold()


def _call_model(reply: str, context: str) -> ModelCall:
    key = config.model_api_key()
    if not key:
        return ModelCall(None, "no model API key configured", 0)

    user = f"{context}\n\nCustomer reply:\n{reply}" if context else reply
    body = {
        "model": config.model_name(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
    }

    last = "unknown"
    spent = 0.0
    for attempt in range(ATTEMPTS):
        try:
            r = httpx.post(
                f"{config.model_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=httpx.Timeout(TIMEOUT_SECONDS, read=TOTAL_TIMEOUT_SECONDS),
            )
        except httpx.HTTPError as e:
            last = f"transport error: {e}"
            log.warning("reply parse attempt %d failed: %s", attempt + 1, e)
            continue

        if r.status_code != 200:
            last = f"HTTP {r.status_code}"
            log.warning("reply parse attempt %d: %s", attempt + 1, last)
            continue

        payload = r.json()
        choice = (payload.get("choices") or [{}])[0]
        spent += float((payload.get("usage") or {}).get("cost") or 0.0)
        content = choice.get("message", {}).get("content")

        # Cut off at the ceiling. Retrying is pointless at temperature 0 — the
        # same input runs out of budget the same way — and it bills three times
        # for one deterministic failure. Report the real cause instead: the
        # response says `length` on every one of these, and reading it is the
        # difference between fixing a budget and blaming a provider.
        if choice.get("finish_reason") == "length":
            log.warning("reply parse attempt %d: truncated at max_tokens", attempt + 1)
            return ModelCall(
                None,
                f"response truncated at max_tokens={MAX_TOKENS} (finish_reason=length)",
                attempt + 1,
                spent,
                truncated=True,
            )

        if content and content.strip():
            return ModelCall(content, None, attempt + 1, spent)

        last = "model returned empty content"
        log.warning("reply parse attempt %d: empty content", attempt + 1)

    return ModelCall(None, last, ATTEMPTS, spent)


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

    call = _call_model(reply, context)
    if call.content is None:
        return base.model_copy(
            update={
                "failed": True,
                "failure_reason": call.failure_reason,
                "attempts": call.attempts,
                "cost_usd": call.cost_usd,
            }
        )

    try:
        data = _extract(call.content)
    except (json.JSONDecodeError, ValueError) as e:
        return base.model_copy(
            update={
                "failed": True,
                "failure_reason": f"model returned unparseable JSON: {e}",
                "attempts": call.attempts,
                "cost_usd": call.cost_usd,
            }
        )

    intents: list[DetectedIntent] = []
    for raw_intent in data.get("intents") or []:
        try:
            evidence = str(raw_intent.get("evidence") or "")
            # The prompt requires a quote from the reply, and until now nothing
            # checked. An empty span gives a reviewer no reason; an invented one
            # shows them words the customer never wrote, with the same standing
            # as a real quote. Dropped rather than coerced, exactly as an intent
            # outside the closed set already is.
            if not _quotes_the_reply(evidence, reply):
                log.warning("dropping intent with unsupported evidence: %r", evidence)
                continue
            intents.append(
                DetectedIntent(
                    type=Intent(raw_intent["type"]),
                    confidence=float(raw_intent.get("confidence", 0.0)),
                    evidence=evidence,
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
            "attempts": call.attempts,
            "cost_usd": call.cost_usd,
        }
    )
