"""Writing to a customer.

The weakest of the model's three jobs, and worth saying so. Templates would
cover most of this; what a model adds is replying in the language the customer
wrote in, and adjusting to somebody who has kept every promise versus somebody
who has broken two.

What it must never add is a fact. The compliance lint in `shell/lint.py` is what
enforces that, and this module's contract with it is simple: if a draft is
blocked, regenerate once with the violation quoted back, and if the second
attempt also fails, send nothing and hand the case to a person. A model that
invents twice will invent a third time, and the point is not to keep asking
until it complies.
"""

from __future__ import annotations

import logging

import httpx

from unhalted import config
from unhalted.shell import lint

log = logging.getLogger("unhalted.core.draft")

ATTEMPTS = 3
TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = """You write short payment reminders for an Indian subscription business.

Rules, and they are absolute:
- State the amount and the merchant name exactly as given. Do not round or rephrase them.
- Include a way to stop the messages. "Reply STOP to opt out" is enough.
- Offer nothing. No discounts, no percentages, no free periods, no waivers, no
  cashback. You have no authority to offer anything and none exists.
- No threats, no legal language, no manufactured urgency, no guarantees.
- Reply in the language the customer last used. Hinglish if they wrote Hinglish.
- Two or three short lines. This is a text message, not a letter.

Return only the message. No preamble, no quotes around it."""


def _call(prompt: str) -> str | None:
    key = config.model_api_key()
    if not key:
        return None
    for attempt in range(ATTEMPTS):
        try:
            r = httpx.post(
                f"{config.model_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": config.model_name(),
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 400,
                    "temperature": 0.3,
                },
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            log.warning("draft attempt %d failed: %s", attempt + 1, e)
            continue
        if r.status_code != 200:
            log.warning("draft attempt %d: HTTP %s", attempt + 1, r.status_code)
            continue
        content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content")
        if content and content.strip():
            return content.strip().strip('"')
    return None


def fallback(amount_paise: int, merchant: str, when: str) -> str:
    """What is sent when the model is unavailable or keeps failing the lint.

    Plainer than a drafted message and passes the lint by construction. Not a
    stub: a customer receiving this gets everything they need.
    """
    return (
        f"Your {merchant} payment of Rs {amount_paise / 100:.0f} did not go through. "
        f"We will try again on {when}.\n"
        f"Reply STOP to opt out of these messages."
    )


def draft_nudge(
    *,
    amount_paise: int,
    merchant: str,
    when: str,
    customer_language: str = "english",
    history: str = "",
) -> tuple[str, list[lint.LintResult]]:
    """Write a nudge that passes the lint, or fall back to one that does.

    Returns the message and every lint result along the way, so a blocked draft
    is visible in the audit trail rather than quietly discarded.
    """
    attempts: list[lint.LintResult] = []
    prompt = (
        f"Merchant: {merchant}\n"
        f"Amount: Rs {amount_paise / 100:.0f}\n"
        f"Next attempt: {when}\n"
        f"Customer writes in: {customer_language}\n"
        f"{history}\n"
        "Write the reminder."
    )

    for round_number in range(2):
        body = _call(prompt)
        if body is None:
            break

        result = lint.check(body, amount_paise=amount_paise, merchant=merchant)
        attempts.append(result)
        if result.passed:
            return body, attempts

        log.warning("draft blocked by compliance lint: %s", result.summary)
        if round_number == 0:
            # One correction, with the violation quoted back. A model that
            # invents twice will invent again.
            prompt += (
                f"\n\nYour previous draft was rejected: {result.summary}. "
                "Write it again without that. Offer nothing."
            )

    return fallback(amount_paise, merchant, when), attempts
