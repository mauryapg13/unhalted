"""Which intervention to try, and when to stop trying.

The ladder runs from free to expensive, and the diagnosis decides where a case
enters it rather than everything starting at the bottom. A broken mandate does
not enter at "silent retry", because no number of silent retries fixes an
expired card — the attempts would be spent proving something already known.

On the expected-value gate
--------------------------
Whether an intervention is worth its cost depends on how often it works, and
**this project has not measured that**. Rather than invent a recovery rate and
let every downstream number inherit it, the gate is split:

- **Provably uneconomic** needs no assumption at all. If a rung costs more than
  the entire amount at stake, it loses money at *any* success rate, because a
  probability cannot exceed 1. A sixty-rupee callback chasing forty-nine rupees
  is a bad idea however optimistic you are.
- **Conditionally uneconomic** needs a success rate, and that rate is a declared
  assumption carried on the decision so a reader can see what it rests on.

C8 replaces the assumed rate with a measured one. Until then, the first kind of
termination is a fact and the second is an estimate, and they are labelled
differently because they are different.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from unhalted.models import DiagnosisClass
from unhalted.policy import POLICY

RUPEE = 100  # paise

#: Read from config/policy.yaml — see unhalted.policy.
LADDER_RULE_VERSION = POLICY.ladder_rule_version


class Rung(int, enum.Enum):
    SILENT_RETRY = 1
    NUDGE = 2
    REAUTHORISATION = 3
    VOICE_CALL = 4
    HUMAN_CALLBACK = 5


#: This module's own name for each rung, in the plain-string terms
#: config/policy.yaml uses — policy.py is not allowed to know Rung exists,
#: so the mapping from its slugs back to this enum lives here. Public: it is
#: also what `agent.py` schedules an action's `kind` as, so that it matches
#: one of `runner.EXECUTORS`'s keys rather than `Intervention.name`, which is
#: prose meant for a human ("message with a pay link"), not a lookup key.
SLUG = {
    Rung.SILENT_RETRY: "silent-retry",
    Rung.NUDGE: "nudge",
    Rung.REAUTHORISATION: "reauthorisation",
    Rung.VOICE_CALL: "voice-call",
    Rung.HUMAN_CALLBACK: "human-callback",
}


def _cost(rung: Rung) -> int:
    return POLICY.ladder_rung_costs_paise[SLUG[rung]]


@dataclass(frozen=True)
class Intervention:
    rung: Rung
    name: str
    cost_paise: int
    why: str
    #: Channels a customer can refuse. A silent retry is not contact.
    is_contact: bool = True


LADDER: dict[Rung, Intervention] = {
    Rung.SILENT_RETRY: Intervention(
        Rung.SILENT_RETRY, "silent retry", _cost(Rung.SILENT_RETRY),
        "costs nothing and disturbs nobody; always worth trying when it can work",
        is_contact=False,
    ),
    Rung.NUDGE: Intervention(
        Rung.NUDGE, "message with a pay link", _cost(Rung.NUDGE),
        "tells the customer something they may not know and gives them a way to act",
    ),
    Rung.REAUTHORISATION: Intervention(
        Rung.REAUTHORISATION, "re-authorisation link", _cost(Rung.REAUTHORISATION),
        "the only path when the mandate itself is the problem",
    ),
    Rung.VOICE_CALL: Intervention(
        Rung.VOICE_CALL, "automated voice call", _cost(Rung.VOICE_CALL),
        "reaches people who do not read messages",
    ),
    Rung.HUMAN_CALLBACK: Intervention(
        Rung.HUMAN_CALLBACK, "human callback", _cost(Rung.HUMAN_CALLBACK),
        "the most effective and by far the most expensive thing available",
    ),
}

#: Where a case joins the ladder, by what went wrong.
ENTRY: dict[DiagnosisClass, Rung | None] = {
    DiagnosisClass.RECOVERABLE_TECHNICAL: Rung.SILENT_RETRY,
    # An empty account is the one failure whose fix depends on a fact no API
    # reports: when the customer will have money. Three silent retries on a
    # fixed schedule spend NPCI's whole allowance guessing at a date one
    # question would have settled — so this asks first, retries against the
    # answer, and falls back to the blind schedule only if nobody replies.
    # `core/reply.py` has always argued this; the ladder just never did it.
    DiagnosisClass.RECOVERABLE_BALANCE: Rung.NUDGE,
    # Re-notifying is the point; a silent retry repeats the failure.
    DiagnosisClass.NOTIFICATION_GAP: Rung.NUDGE,
    # Retries cannot succeed against a dead mandate. Skip straight to fixing it.
    DiagnosisClass.MANDATE_STATE_BROKEN: Rung.REAUTHORISATION,
    # Nothing to recover. The customer withdrew permission.
    DiagnosisClass.CUSTOMER_INTENT_REVOKED: None,
    DiagnosisClass.UNKNOWN: None,
}

#: Success rates per rung. **These are merchant policy, not measurements, and
#: this project cannot measure them.**
#:
#: Measuring them needs real recovery outcomes at volume. A generated batch
#: cannot supply that: whoever writes the batch's outcome model decides the
#: rates, and reading them back out is circular. C8 does not fix this and it was
#: wrong of an earlier note here to say it would.
#:
#: What a merchant has that this project does not is their own history. The
#: values below are a deliberately conservative starting point so the gate has
#: somewhere to begin, and any decision resting on one is flagged
#: `assumption_used` so a reader can see what it rests on. Override them per
#: deployment; do not treat them as findings.
DEFAULT_SUCCESS: dict[Rung, float] = {
    Rung.SILENT_RETRY: 0.20,
    Rung.NUDGE: 0.25,
    Rung.REAUTHORISATION: 0.30,
    Rung.VOICE_CALL: 0.35,
    Rung.HUMAN_CALLBACK: 0.50,
}


@dataclass(frozen=True)
class LadderDecision:
    rung: Rung | None
    approved: bool
    reason: str
    calculation: str = ""
    assumption_used: bool = False
    rules_fired: list[str] = field(default_factory=list)
    rule_version: str = LADDER_RULE_VERSION


def entry_rung(klass: DiagnosisClass) -> Rung | None:
    return ENTRY.get(klass)


def evaluate(
    rung: Rung,
    amount_paise: int,
    *,
    customer_ltv_paise: int | None = None,
    refused_channels: frozenset[str] = frozenset(),
    success_rates: dict[Rung, float] | None = None,
) -> LadderDecision:
    """Whether this rung is worth trying for this amount.

    `success_rates` is the merchant's, and the default is a conservative
    placeholder rather than a finding. Decisions that used it say so.
    """
    intervention = LADDER[rung]
    rules: list[str] = []

    if intervention.name in refused_channels or (
        rung is Rung.VOICE_CALL and "voice" in refused_channels
    ):
        return LadderDecision(
            rung=rung, approved=False,
            reason=f"the customer asked not to be reached by {intervention.name}",
            rules_fired=["CHANNEL_REFUSED"],
        )

    if intervention.cost_paise == 0:
        return LadderDecision(
            rung=rung, approved=True, reason="costs nothing",
            rules_fired=["FREE"],
        )

    # Provable, and needing no assumption: a probability cannot exceed 1, so a
    # rung costing more than the whole amount loses money at every success rate.
    stake = max(amount_paise, customer_ltv_paise or 0)
    if intervention.cost_paise >= stake:
        return LadderDecision(
            rung=rung, approved=False,
            reason="costs more than the entire amount at stake, at any success rate",
            calculation=(
                f"cost Rs {intervention.cost_paise / RUPEE:.0f} >= "
                f"stake Rs {stake / RUPEE:.0f}"
            ),
            rules_fired=["UNECONOMIC:PROVABLE"],
        )

    # Conditional, and resting on a rate this project cannot measure. The
    # merchant supplies it or accepts the conservative default.
    rate = (success_rates or DEFAULT_SUCCESS)[rung]
    expected = rate * stake
    source = "merchant's" if success_rates else "default, unmeasured"
    calculation = (
        f"{source} success rate {rate:.0%} x stake Rs {stake / RUPEE:.0f} "
        f"= Rs {expected / RUPEE:.2f} against cost Rs {intervention.cost_paise / RUPEE:.0f}"
    )
    if expected <= intervention.cost_paise:
        return LadderDecision(
            rung=rung, approved=False,
            reason=(
                "expected recovery does not cover the cost, on a success rate this "
                "project did not measure"
            ),
            calculation=calculation,
            assumption_used=True,
            rules_fired=["UNECONOMIC:ASSUMED"],
        )

    if customer_ltv_paise and customer_ltv_paise > amount_paise * 4:
        rules.append("LTV_JUSTIFIED")

    return LadderDecision(
        rung=rung, approved=True,
        reason=(
            "expected recovery exceeds the cost, on a success rate this project "
            "did not measure"
        ),
        calculation=calculation,
        assumption_used=True,
        rules_fired=rules or ["ECONOMIC"],
    )


def next_rung(current: Rung, *, refused_channels: frozenset[str] = frozenset()) -> Rung | None:
    """The next step up, skipping anything the customer has refused."""
    for value in range(current.value + 1, Rung.HUMAN_CALLBACK.value + 1):
        candidate = Rung(value)
        if candidate is Rung.VOICE_CALL and "voice" in refused_channels:
            continue
        return candidate
    return None
