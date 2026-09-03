"""The money question, answered without inventing an answer.

"How much did you recover" is the first thing anyone asks, and this project
cannot answer it. Recovery needs real outcomes at volume; on generated data,
whoever writes the outcome model decides the result, and a rupee figure produced
that way is worth less than no figure at all because it discredits the sound
numbers standing next to it.

So this inverts the unknown. Instead of guessing a conversion rate and reporting
the rupees, it reports **the conversion rate at which the rupees stop being
worth chasing**. That is arithmetic on a measured cost and a documented failure
class, it is checkable by anyone, and a merchant can hold it against their own
history in seconds.

Three claims, in descending order of strength
---------------------------------------------
**Sorting.** Money is split by what each policy can *reach*, using Razorpay's
own error descriptions. A retry cannot fix a mandate that no longer exists —
that is their documentation, not our judgement — so the baseline recovers
exactly zero on that money however long it keeps trying.

**Dominance.** On that same money the agent recovers some amount ≥ 0, because
re-authorisation is a path that can work where a retry provably cannot. The
*magnitude* is unknown; the *sign* is not. The agent is weakly better on that
share, and strictly better the moment re-authorisation works even once.

**Breakeven.** The intervention has a real, known cost. Divide it by the money
it is placed in front of and you have the conversion rate below which it loses
money. No behavioural assumption is involved at any point.

What is deliberately absent
---------------------------
Rupees recovered as a point estimate. Also any published benchmark borrowed as
a substitute for measurement: card-updater recovery in the US is a different
mechanism from an Indian re-authorisation that needs the customer to open an app
and enter an MPIN, and importing one would be the same invention with a footnote
attached to it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from unhalted.models import DiagnosisClass
from unhalted.shell.ladder import LADDER, Rung

RUPEE = 100

#: Where a retry provably cannot succeed, from Razorpay's own error
#: descriptions. The baseline spends every attempt it has here and recovers
#: nothing; this is the money only a different action can reach.
UNREACHABLE_BY_RETRY = DiagnosisClass.MANDATE_STATE_BROKEN

#: Consent was withdrawn. Neither policy should recover this, and taking it
#: would be the failure, not the success.
MUST_NOT_TAKE = DiagnosisClass.CUSTOMER_INTENT_REVOKED


@dataclass
class Exposure:
    """Money at risk, sorted by which policy can reach it."""

    cases: int = 0
    total_paise: int = 0

    #: A retry cannot work. Baseline recovers zero here by construction.
    unreachable_paise: int = 0
    unreachable_cases: int = 0

    #: The customer revoked. Success would be a compliance failure.
    must_not_take_paise: int = 0
    must_not_take_cases: int = 0

    #: No rule matched, so a person decides. Not a policy difference — a gap.
    unclassified_paise: int = 0
    unclassified_cases: int = 0

    #: Both policies attempt these. No honest claim without a rate.
    contested_paise: int = 0
    contested_cases: int = 0

    def share(self, paise: int) -> float:
        return paise / self.total_paise if self.total_paise else 0.0


@dataclass(frozen=True)
class Breakeven:
    """The conversion rate at which an intervention repays its own cost."""

    rung: Rung
    cases: int
    spend_paise: int
    exposure_paise: int
    #: spend / exposure. Below this the intervention loses money.
    rate: float
    #: How many customers of average size must pay to cover the whole spend.
    #: Often less than one, which is the clearest way to say it out loud.
    customers_needed: float
    #: Rupees of exposure per rupee spent.
    leverage: float

    @property
    def mean_amount_paise(self) -> float:
        return self.exposure_paise / self.cases if self.cases else 0.0


@dataclass(frozen=True)
class Envelope:
    """The most either policy could recover, at perfect conversion.

    A ceiling, not an estimate. Neither policy reaches it; the point is that
    the baseline's is lower by exactly the money a retry cannot fix, and no
    amount of retrying raises it.
    """

    baseline_ceiling_paise: int
    agent_ceiling_paise: int
    #: The gap, which is the money the whole argument is about.
    dominance_paise: int
    notes: list[str] = field(default_factory=list)


def classify(items: Iterable[tuple[int, DiagnosisClass]]) -> Exposure:
    """Sort (amount_paise, diagnosis class) pairs into what each policy reaches.

    Source-agnostic on purpose: a generated batch and a table of real stored
    cases both reduce to the same pairs, so the same arithmetic serves both.
    """
    out = Exposure()
    for amount, klass in items:
        out.cases += 1
        out.total_paise += amount
        if klass is UNREACHABLE_BY_RETRY:
            out.unreachable_paise += amount
            out.unreachable_cases += 1
        elif klass is MUST_NOT_TAKE:
            out.must_not_take_paise += amount
            out.must_not_take_cases += 1
        elif klass is DiagnosisClass.UNKNOWN:
            out.unclassified_paise += amount
            out.unclassified_cases += 1
        else:
            out.contested_paise += amount
            out.contested_cases += 1
    return out


def breakeven(
    exposure: Exposure,
    *,
    rung: Rung = Rung.REAUTHORISATION,
) -> Breakeven:
    """What fraction must convert before the intervention pays for itself.

    Defaults to re-authorisation against the unreachable money, because that is
    the pairing the argument rests on: the one action that can work, placed in
    front of the one bucket a retry provably cannot.
    """
    cost_each = LADDER[rung].cost_paise
    spend = exposure.unreachable_cases * cost_each
    money = exposure.unreachable_paise

    rate = spend / money if money else 0.0
    mean = money / exposure.unreachable_cases if exposure.unreachable_cases else 0.0
    return Breakeven(
        rung=rung,
        cases=exposure.unreachable_cases,
        spend_paise=spend,
        exposure_paise=money,
        rate=rate,
        customers_needed=(spend / mean) if mean else 0.0,
        leverage=(money / spend) if spend else 0.0,
    )


def envelope(exposure: Exposure) -> Envelope:
    """The ceiling on each policy, and the gap between them.

    The baseline cannot reach the money a retry cannot fix — that is Razorpay's
    documentation, not a claim about customers. Neither policy may take revoked
    money. Everything else is reachable in principle by both.
    """
    baseline = exposure.contested_paise + exposure.unclassified_paise
    agent = baseline + exposure.unreachable_paise
    return Envelope(
        baseline_ceiling_paise=baseline,
        agent_ceiling_paise=agent,
        dominance_paise=exposure.unreachable_paise,
        notes=[
            "Ceilings, not estimates. Neither policy reaches its own.",
            "Revoked money is excluded from both: recovering it would be the failure.",
            "The gap is exactly the money a retry provably cannot fix.",
        ],
    )


def render_outcomes(exposure: Exposure, be: Breakeven, env: Envelope) -> str:
    """The three claims, in plain text, with the absent one named."""
    r = RUPEE

    def row(label: str, paise: int, cases: int) -> str:
        return (
            f"  {label:<34}Rs {paise / r:>11,.0f}{exposure.share(paise):>7.0%}"
            f"{cases:>8}"
        )

    lines = [
        "",
        f"MONEY AT RISK   Rs {exposure.total_paise / r:,.0f} across {exposure.cases} cases",
        "",
        f"  {'':<34}{'amount':>14}{'share':>7}{'cases':>8}",
        "  " + "-" * 62,
        row("unreachable by retry", exposure.unreachable_paise, exposure.unreachable_cases),
        row("must NOT be taken", exposure.must_not_take_paise, exposure.must_not_take_cases),
        row("unclassified, a person decides", exposure.unclassified_paise,
            exposure.unclassified_cases),
        row("retry not ruled out", exposure.contested_paise, exposure.contested_cases),
        "",
        "  'Unreachable' is Razorpay's own wording: a retry cannot fix a mandate that no",
        "  longer exists. Their policy spends three attempts there and recovers nothing.",
        "  'Retry not ruled out' is not 'recoverable' — both policies already attempt it.",
        "",
        "CEILINGS",
        "",
        f"  Razorpay's policy, at perfect conversion   Rs {env.baseline_ceiling_paise / r:>11,.0f}",
        f"  unhalted, at perfect conversion           Rs {env.agent_ceiling_paise / r:>11,.0f}",
        f"  the gap                                   Rs {env.dominance_paise / r:>11,.0f}",
        "",
        "  Neither policy reaches its own ceiling. What is provable is the ordering: the",
        "  agent is weakly better on the gap, and strictly better the moment",
        "  re-authorisation works even once.",
        "",
        f"BREAKEVEN — {LADDER[be.rung].name}",
        "",
        f"  cases                                     {be.cases:>14,}",
        (f"  cost                                      Rs {be.spend_paise / r:>11,.0f}"
         f"   (Rs {LADDER[be.rung].cost_paise / r:.0f} each)"),
        f"  money it is placed in front of            Rs {be.exposure_paise / r:>11,.0f}",
        f"  average per case                          Rs {be.mean_amount_paise / r:>11,.0f}",
        "",
        f"  BREAKS EVEN AT                            {be.rate:>13.3%}",
        f"  customers who must pay to cover it        {be.customers_needed:>14.2f}",
        f"  exposure per rupee spent                  {be.leverage:>13,.0f}x",
        "",
        "  Every figure above is arithmetic on a measured cost and a documented failure",
        "  class. None of it assumes anything about how customers behave.",
        "",
        "NOT REPORTED",
        "",
        "  Rupees recovered. That needs real outcomes at volume, and this runs on a test",
        "  account. Supply a conversion rate and the figure is a multiplication — but the",
        "  rate is then yours, and it should be named as yours wherever the number appears.",
        "",
    ]
    return "\n".join(lines)
