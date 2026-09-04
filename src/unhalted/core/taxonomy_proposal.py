"""Propose a taxonomy rule for an error reason nothing currently classifies.

Every case that reaches `diagnose()` with no matching rule is held for a
human, and — until now — that was the end of it: nothing fed the held case
back toward actually closing the gap. This reads Razorpay's own documentation
for the reason in question and proposes a rule, in the same shape
`policy_change.py` proposes a config change: grounded in a quote from the
text, never applied, always a human's edit to make.

If the supplied text does not actually state a root cause for this reason,
the honest answer is that no rule can be proposed yet — not a guessed one.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging

import httpx

from unhalted import config
from unhalted.core.evidence import quotes_the_source
from unhalted.models import DiagnosisClass

log = logging.getLogger("unhalted.core.taxonomy_proposal")

ATTEMPTS = 3
TIMEOUT_SECONDS = 30
TOTAL_TIMEOUT_SECONDS = 90
MAX_TOKENS = 1500

SYSTEM_PROMPT = """You read Razorpay's own documentation for one payment error reason and propose
a taxonomy rule for it, in the same style this project already uses for every other rule.

Reply with ONLY a JSON object. No prose, no markdown fences, no explanation.

{
  "proposable": true | false,
  "klass": "<one of the classes below, or null if not proposable>",
  "directness": "direct" | "inferred" | null,
  "quote": "<the exact words in the text that state the cause, or empty if not proposable>",
  "rationale": "<one sentence, in this project's own voice, or the reason nothing can be proposed>"
}

The classes, and nothing outside this list:
  recoverable-technical    a transient technical or bank-side failure; retrying can work
  recoverable-balance      the account did not have enough funds; retrying later can work
  notification-gap         the customer was not adequately notified or did not act in time
  mandate-state-broken     the mandate itself cannot execute (expired, blocked, invalid); no
                           retry can fix it, only re-authorisation
  customer-intent-revoked  the customer withdrew consent; nothing should be attempted
  unknown                  the text does not support classifying this at all

Rules:
- "direct" only when Razorpay's own words state the cause. "inferred" when you are reading
  between the lines. Never claim "direct" for a reason their text says is ambiguous or
  unattributed — read the taxonomy's own existing rationale style if you have seen it: it treats
  "the exact reason is not communicated" as grounds for "inferred", not "direct".
- "quote" must be words that actually appear in the text. Do not paraphrase.
- If the text does not mention this error reason at all, or mentions it without stating a cause,
  set "proposable" to false, "klass" to null, and say so in "rationale" — do not guess a
  plausible-sounding class from the reason's name alone."""


def prompt_hash() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]


def _extract(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(text.strip())


@dataclasses.dataclass(frozen=True)
class ModelCall:
    content: str | None
    failure_reason: str | None
    attempts: int
    cost_usd: float = 0.0
    truncated: bool = False


@dataclasses.dataclass(frozen=True)
class RuleProposal:
    """A candidate taxonomy rule. Never written to taxonomy.py — a human adds
    it, the same way a policy change is a human's edit to make."""

    method: str
    error_reason: str
    error_source: str | None
    error_step: str | None
    proposable: bool
    klass: DiagnosisClass | None
    directness: float | None
    quote: str
    rationale: str
    cost_usd: float
    failed: bool = False
    failure_reason: str | None = None
    prompt_hash: str | None = None


def _call_model(reason: str, doc_text: str) -> ModelCall:
    key = config.model_api_key()
    if not key:
        return ModelCall(None, "no model API key configured", 0)

    user = f"error_reason: {reason}\n\nRazorpay's documentation:\n{doc_text}"
    body = {
        "model": config.model_name(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "reasoning": {"effort": "low"},
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
            log.warning("taxonomy-proposal attempt %d failed: %s", attempt + 1, e)
            continue

        if r.status_code != 200:
            last = f"HTTP {r.status_code}"
            log.warning("taxonomy-proposal attempt %d: %s", attempt + 1, last)
            continue

        payload = r.json()
        choice = (payload.get("choices") or [{}])[0]
        spent += float((payload.get("usage") or {}).get("cost") or 0.0)
        content = choice.get("message", {}).get("content")

        if choice.get("finish_reason") == "length":
            log.warning("taxonomy-proposal attempt %d: truncated at max_tokens", attempt + 1)
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
        log.warning("taxonomy-proposal attempt %d: empty content", attempt + 1)

    return ModelCall(None, last, ATTEMPTS, spent)


def propose(
    *, method: str, error_reason: str, error_source: str | None = None,
    error_step: str | None = None, doc_text: str,
) -> RuleProposal:
    """Read `doc_text` and propose a rule for `error_reason`, or say why not."""

    def _refused(reason: str, cost: float = 0.0) -> RuleProposal:
        return RuleProposal(
            method=method, error_reason=error_reason, error_source=error_source,
            error_step=error_step, proposable=False, klass=None, directness=None,
            quote="", rationale="", cost_usd=cost, failed=True, failure_reason=reason,
        )

    call = _call_model(error_reason, doc_text)
    if call.content is None:
        return _refused(call.failure_reason, call.cost_usd)

    try:
        data = _extract(call.content)
    except (json.JSONDecodeError, ValueError) as e:
        return _refused(f"model returned unparseable JSON: {e}", call.cost_usd)

    if not data.get("proposable"):
        return RuleProposal(
            method=method, error_reason=error_reason, error_source=error_source,
            error_step=error_step, proposable=False, klass=None, directness=None,
            quote="", rationale=str(data.get("rationale") or "no cause stated in the text"),
            cost_usd=call.cost_usd, prompt_hash=prompt_hash(),
        )

    quote = str(data.get("quote") or "")
    if not quotes_the_source(quote, doc_text):
        return _refused("model proposed a class with no supporting quote in the text",
                        call.cost_usd)

    try:
        klass = DiagnosisClass(data.get("klass"))
    except ValueError:
        return _refused(f"model proposed an unknown class: {data.get('klass')!r}", call.cost_usd)

    directness = 1.0 if data.get("directness") == "direct" else 0.8

    return RuleProposal(
        method=method, error_reason=error_reason, error_source=error_source,
        error_step=error_step, proposable=True, klass=klass, directness=directness,
        quote=quote, rationale=str(data.get("rationale") or ""),
        cost_usd=call.cost_usd, prompt_hash=prompt_hash(),
    )
