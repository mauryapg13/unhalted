"""Generating a batch of failures to measure two policies against.

Everything here is synthetic and says so. What is *not* invented is the shape:
the error reasons, their sources and steps, and their relative frequency come
from Razorpay's published error taxonomy — the same generated data the diagnosis
runs on, pinned to a commit of their docs.

Why generate at all
-------------------
A test account produces one real failure per human click and only ever the
generic `payment_failed`. Both checkout surfaces were tested; neither yields the
specific reasons. So a batch large enough to compare two policies has to be
built, and the honest thing is to say exactly which parts are real.

What this cannot do
-------------------
Decide whether anything recovered. Nothing here models an outcome, because
whoever writes that model decides the answer. The measurements this batch
supports are facts about what each policy *does* — attempts spent, windows
violated, messages sent — and those need no outcome at all.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from unhalted.core.taxonomy import DATA_FILE
from unhalted.models import FailureSignal
from unhalted.shell.windows import IST

#: Roughly what a subscription business sees, and the one judgement call in this
#: module. Real traffic skews heavily toward empty accounts; the long tail of
#: mandate and instrument problems is smaller but individually more expensive.
#: Weighted by hand and labelled as such — nobody measured this either.
REASON_WEIGHTS: dict[str, float] = {
    "insufficient_funds": 0.42,
    "payment_failed": 0.14,
    "payment_declined": 0.08,
    "gateway_technical_error": 0.07,
    "bank_technical_error": 0.06,
    "payment_timed_out": 0.06,
    "card_expired": 0.05,
    "authentication_failed": 0.04,
    "debit_instrument_blocked": 0.03,
    "invalid_vpa": 0.02,
    "card_disabled_for_online_payments": 0.02,
    "payment_risk_check_failed": 0.01,
}

#: Subscription prices an Indian merchant actually charges.
AMOUNTS_PAISE = [4900, 9900, 14900, 19900, 29900, 49900, 79900, 99900]

SOURCES = ["customer", "bank", "issuer", "gateway", None]
STEPS = ["payment_authorization", "payment_authentication", "payment_initiation"]


@dataclass(frozen=True)
class GeneratedCase:
    signal: FailureSignal
    #: Which cohort this case belongs to. Assigned before anything is decided.
    holdout: bool


def _methods_for(reason: str) -> list[str]:
    data = json.loads(DATA_FILE.read_text())
    methods = [m for m in ("card", "upi") if reason in data["reasons"].get(m, {})]
    return methods or ["card"]


def generate(
    count: int,
    *,
    holdout_pct: int = 10,
    seed: int = 20260902,
) -> list[GeneratedCase]:
    """Build a reproducible batch.

    Seeded so a reported number can be regenerated exactly. Cohort assignment
    happens here, before any policy sees a case, so neither can be favoured by
    the order things were decided in.
    """
    rng = random.Random(seed)
    reasons = list(REASON_WEIGHTS)
    weights = [REASON_WEIGHTS[r] for r in reasons]
    start = datetime(2026, 9, 1, 9, 0, tzinfo=IST)

    cases: list[GeneratedCase] = []
    for n in range(count):
        reason = rng.choices(reasons, weights=weights, k=1)[0]
        method = rng.choice(_methods_for(reason))
        # Spread across the clock so NPCI's restricted bands are exercised by
        # roughly the share of real traffic that would fall inside them.
        occurred = start + timedelta(minutes=rng.randrange(0, 60 * 24 * 30))
        signal = FailureSignal(
            payment_id=f"pay_GEN{n:05d}",
            order_id=f"order_GEN{n:05d}",
            customer_ref=f"cust_gen_{n % max(1, count // 3):04d}",
            amount_paise=rng.choice(AMOUNTS_PAISE),
            method=method,
            error_reason=reason,
            error_source=rng.choice(SOURCES),
            error_step=rng.choice(STEPS),
            occurred_at=occurred,
            source="generated:razorpay-taxonomy",
        )
        cases.append(GeneratedCase(signal=signal, holdout=rng.random() < holdout_pct / 100))
    return cases
