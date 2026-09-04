"""Propose a change to `config/policy.yaml` from a pasted regulatory text.

Never applies anything. NPCI and RBI requirements change, and this system's
enforcement lives in one file for exactly that reason — but a person still
reads the circular, and a person still decides. This reads free text (a
circular, a notice, an internal memo) and proposes a specific field-level
diff against the policy currently in force. Nothing here writes to
`config/policy.yaml`; `scripts/propose_policy_change.py` prints the proposal
for a human to apply by hand, the same distance between recommending and
acting every other model call in this project keeps.

Same architecture as reply parsing: the model reads, it proposes, and every
proposed value is checked against the text it was supposedly read from
rather than trusted. A field outside the fixed set below is refused before
the model is ever asked — this is not a general-purpose config editor, it
is scoped to the handful of numbers a real circular plausibly states.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging

import httpx

from unhalted import config, policy
from unhalted.core.evidence import quotes_the_source

log = logging.getLogger("unhalted.core.policy_change")

ATTEMPTS = 3
TIMEOUT_SECONDS = 30
TOTAL_TIMEOUT_SECONDS = 90
MAX_TOKENS = 2000

#: The only fields a circular may propose changing, and how to validate a
#: proposed value for each. Deliberately not every field in policy.yaml —
#: backoff tiers, confidence thresholds and reply-policy thresholds are this
#: project's own risk tolerance, not something NPCI or RBI states, and stay
#: off this list so a circular cannot be used to relax them.
ALLOWED_FIELDS = (
    "npci.restricted_bands",
    "retries.cap",
    "contact.open",
    "contact.close",
    "limits.frictionless_upi_rupees",
    "limits.frictionless_upi_bfsi_rupees",
    "limits.upi_mandate_max_rupees",
    "limits.card_recurring_max_rupees",
    "limits.emandate_max_rupees",
)

SYSTEM_PROMPT = f"""You read a regulatory or internal notice describing a change to a payments
policy, and propose a specific, minimal change to a fixed set of fields. You do not decide
anything — a person reviews every proposal against the quote you give for it before anything
changes.

Reply with ONLY a JSON object. No prose, no markdown fences, no explanation.

{{
  "changes": [
    {{
      "field": "<one of the allowed fields below>",
      "proposed_value": <the new value, in the same shape the field already has>,
      "quote": "<the exact words in the text that state this change>",
      "reasoning": "<one sentence>"
    }}
  ],
  "unclear": ["<something the text seems to reference but gives no usable number for>"]
}}

The only fields you may propose changing, and nothing else:
{chr(10).join(f"  {f}" for f in ALLOWED_FIELDS)}

Rules:
- "quote" must be words that actually appear in the text. Do not paraphrase, and do not
  construct a quote by joining words from different sentences.
- Propose a field only when the text states a specific new value for it. A field merely
  mentioned, or discussed only in general terms, goes in "unclear" instead — with a plain-
  language note of what is missing, not a guessed number.
- "npci.restricted_bands" is a list of [start, end] pairs, each "HH:MM" in IST, e.g.
  [["10:00", "13:30"], ["17:00", "21:30"]]. Only propose it if the text gives exact times for
  every band it wants in force, not just one band being extended.
- "contact.open" and "contact.close" are "HH:MM" in IST.
- "retries.cap" is a whole number.
- Every "*_rupees" field is a plain number of rupees, not paise, and not a string.
- Never invent a field not in the list above. If the text describes a change this schema has
  no field for, put it in "unclear" and say so — do not force it onto the nearest field."""


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


def _current_value(field: str) -> object:
    """What this field is set to right now, read from the live policy — never
    from the model, which could misstate it as easily as it could a change."""
    p = policy.POLICY
    return {
        "npci.restricted_bands": [
            [a.strftime("%H:%M"), b.strftime("%H:%M")] for a, b in p.npci_restricted_bands
        ],
        "retries.cap": p.retry_cap,
        "contact.open": p.contact_open.strftime("%H:%M"),
        "contact.close": p.contact_close.strftime("%H:%M"),
        "limits.frictionless_upi_rupees": p.frictionless_upi_paise // policy.RUPEE,
        "limits.frictionless_upi_bfsi_rupees": p.frictionless_upi_bfsi_paise // policy.RUPEE,
        "limits.upi_mandate_max_rupees": p.upi_mandate_max_paise // policy.RUPEE,
        "limits.card_recurring_max_rupees": p.card_recurring_max_paise // policy.RUPEE,
        "limits.emandate_max_rupees": p.emandate_max_paise // policy.RUPEE,
    }[field]


@dataclasses.dataclass(frozen=True)
class ProposedChange:
    field: str
    current_value: object
    proposed_value: object
    quote: str
    reasoning: str


@dataclasses.dataclass(frozen=True)
class Proposal:
    """The model's read of a circular. Nothing here has been applied."""

    changes: tuple[ProposedChange, ...]
    unclear: tuple[str, ...]
    #: Proposals the model made that were refused before being shown to a
    #: human — an invented field, or a quote that does not appear in the
    #: text — with why, so refusing is visible rather than silent.
    dropped: tuple[str, ...]
    cost_usd: float
    failed: bool = False
    failure_reason: str | None = None
    prompt_hash: str | None = None


def _call_model(text: str) -> ModelCall:
    key = config.model_api_key()
    if not key:
        return ModelCall(None, "no model API key configured", 0)

    body = {
        "model": config.model_name(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        # See the identical note in core/reply.py: this model spends most of
        # its completion budget on invisible reasoning tokens, which is what
        # actually drives the call-to-call latency swings. "low" measured
        # 3-9x faster with no accuracy loss on real test cases.
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
            log.warning("policy-change parse attempt %d failed: %s", attempt + 1, e)
            continue

        if r.status_code != 200:
            last = f"HTTP {r.status_code}"
            log.warning("policy-change parse attempt %d: %s", attempt + 1, last)
            continue

        payload = r.json()
        choice = (payload.get("choices") or [{}])[0]
        spent += float((payload.get("usage") or {}).get("cost") or 0.0)
        content = choice.get("message", {}).get("content")

        if choice.get("finish_reason") == "length":
            log.warning("policy-change parse attempt %d: truncated at max_tokens", attempt + 1)
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
        log.warning("policy-change parse attempt %d: empty content", attempt + 1)

    return ModelCall(None, last, ATTEMPTS, spent)


def propose(text: str) -> Proposal:
    """Read `text` and propose changes. Applies nothing, ever."""
    call = _call_model(text)
    if call.content is None:
        return Proposal((), (), (), call.cost_usd, failed=True, failure_reason=call.failure_reason)

    try:
        data = _extract(call.content)
    except (json.JSONDecodeError, ValueError) as e:
        return Proposal(
            (), (), (), call.cost_usd, failed=True,
            failure_reason=f"model returned unparseable JSON: {e}",
        )

    changes: list[ProposedChange] = []
    dropped: list[str] = []
    for raw in data.get("changes") or []:
        field = str(raw.get("field") or "")
        quote = str(raw.get("quote") or "")
        proposed = raw.get("proposed_value")

        if field not in ALLOWED_FIELDS:
            dropped.append(f"{field!r} is not a field this system will let a circular change")
            log.warning("policy-change: refusing unknown field %r", field)
            continue
        if not quotes_the_source(quote, text):
            dropped.append(f"{field}: proposed {proposed!r} with no supporting quote in the text")
            log.warning("policy-change: dropping %s, unsupported quote %r", field, quote)
            continue

        changes.append(ProposedChange(
            field=field,
            current_value=_current_value(field),
            proposed_value=proposed,
            quote=quote,
            reasoning=str(raw.get("reasoning") or ""),
        ))

    unclear = tuple(str(u) for u in (data.get("unclear") or []))
    return Proposal(
        tuple(changes), unclear, tuple(dropped), call.cost_usd,
        prompt_hash=prompt_hash(),
    )
