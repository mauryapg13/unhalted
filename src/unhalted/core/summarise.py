"""Preparing a held case for the person who has to decide it.

A held case costs a human about sixty rupees of their time, and most of that is
spent reading. The signals, the diagnosis, the reasoning, the customer's words —
all present, none of it arranged for somebody who has thirty seconds.

So the model reads the record and states what it thinks, with what it weighed.
It decides nothing. The reviewer still approves, rejects or reclassifies, and
the summary is labelled as the agent's opinion so nobody mistakes it for a
finding.

The specification asks for exactly this: "queued for human review with the
agent's best-guess hypothesis attached".
"""

from __future__ import annotations

import logging

import httpx

from unhalted import config

log = logging.getLogger("unhalted.core.summarise")

TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = """You brief a human reviewer on a failed subscription payment they must decide about.

They have thirty seconds. Write at most four short lines:

1. What happened, in plain words.
2. Why it stopped here rather than being handled automatically.
3. Your best guess at what should happen, and how sure you are.
4. The one thing you would want to know that the record does not say.

Rules:
- You are advising, not deciding. Say "looks like" and "probably", not "this is".
- Never invent a fact. If the record does not say it, say the record does not say it.
- No recommendations involving offers, discounts or waivers. None exist.

Return only the briefing."""


def brief(case_summary: str) -> str | None:
    """Summarise a held case, or return None if the model is unavailable.

    None is not an error state. A reviewer without a briefing still has the full
    record; a reviewer with a fabricated briefing has something worse.
    """
    key = config.model_api_key()
    if not key:
        return None
    try:
        r = httpx.post(
            f"{config.model_base_url()}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": config.model_name(),
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": case_summary},
                ],
                "max_tokens": 500,
                "temperature": 0.2,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        log.warning("could not brief the reviewer: %s", e)
        return None
    if r.status_code != 200:
        return None
    content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content")
    return content.strip() if content and content.strip() else None
