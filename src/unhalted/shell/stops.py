"""The stop rules. Nine of them, and none is overridable.

A stop is not a recommendation the shell weighs against others. It is a fact
about the case that ends automated action, and no confidence from any component
lifts it. That is the point of them being here rather than anywhere a model can
reach.

Each carries the code the specification names, so the audit line reads
`STOP_RULE:REVOKED` and an auditor can match it to the rule that fired.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import timedelta

from unhalted.models import CaseState


class StopScope(str, enum.Enum):
    """How far a stop reaches.

    Scope matters as much as the trigger. A chargeback freezes everything for
    that customer, not just the case it arrived on — continuing to chase someone
    who has disputed a charge is how a complaint becomes a regulatory problem.
    """

    CASE = "case"
    CUSTOMER = "customer"
    MERCHANT = "merchant"


@dataclass(frozen=True)
class StopRule:
    code: str
    trigger: str
    action: str
    scope: StopScope
    sla: timedelta | None
    terminal_state: CaseState | None
    suppresses_contact: bool
    why: str


#: The nine stops, in the specification's order.
RULES: dict[str, StopRule] = {
    "REVOKED": StopRule(
        code="REVOKED",
        trigger="mandate revoked via the customer's UPI app",
        action="cancel every pending action",
        scope=StopScope.CUSTOMER,
        sla=timedelta(seconds=60),
        terminal_state=CaseState.CLOSED_REVOKED,
        suppresses_contact=True,
        why="the customer withdrew permission to debit; there is nothing left to recover against",
    ),
    "OPT_OUT": StopRule(
        code="OPT_OUT",
        trigger="opt-out intent parsed at confidence >= 0.70",
        action="suppress all automated contact",
        scope=StopScope.CUSTOMER,
        sla=timedelta(seconds=60),
        terminal_state=None,
        suppresses_contact=True,
        why="they asked not to be contacted; continuing is a compliance failure, not a lost sale",
    ),
    "DISPUTE": StopRule(
        code="DISPUTE",
        trigger="dispute intent parsed",
        action="halt debits and nudges, and route to a human",
        scope=StopScope.CUSTOMER,
        sla=timedelta(seconds=60),
        # Held, not closed. The claim is verified against transaction history,
        # any refund needs human approval, and recovery resumes only after the
        # customer confirms the adjustment. None of that happens on its own.
        terminal_state=CaseState.HELD_FOR_HUMAN,
        suppresses_contact=True,
        why="chasing someone who says you took their money twice turns a complaint into a chargeback",
    ),
    "DISTRESS": StopRule(
        code="DISTRESS",
        trigger="distress sentiment detected",
        action="halt automation and tag the case human-only",
        scope=StopScope.CASE,
        sla=timedelta(seconds=60),
        terminal_state=CaseState.HELD_FOR_HUMAN,
        suppresses_contact=True,
        why="automated dunning of someone in hardship is wrong before it is unwise",
    ),
    "RETRY_CAP": StopRule(
        code="RETRY_CAP",
        trigger="the retry cap of 3 is reached",
        action="end retries and enter the escalation ladder",
        scope=StopScope.CASE,
        sla=None,
        terminal_state=None,
        suppresses_contact=False,
        why="NPCI allows one execution and three retries per cycle; a fourth is not ours to make",
    ),
    "LADDER_END": StopRule(
        code="LADDER_END",
        trigger="the escalation ladder is exhausted",
        action="close the case as unrecovered",
        scope=StopScope.CASE,
        sla=None,
        terminal_state=CaseState.UNRECOVERED,
        suppresses_contact=True,
        why="every intervention has been tried; continuing spends money to no end",
    ),
    "CHARGEBACK": StopRule(
        code="CHARGEBACK",
        trigger="a chargeback is raised on the mandate",
        action="freeze all recovery for the customer, and route to a human",
        scope=StopScope.CUSTOMER,
        sla=timedelta(minutes=5),
        terminal_state=CaseState.HELD_FOR_HUMAN,
        suppresses_contact=True,
        why="the dispute is now formal; any further contact becomes evidence",
    ),
    "MERCHANT_PAUSE": StopRule(
        code="MERCHANT_PAUSE",
        trigger="the merchant pauses their recovery programme",
        action="freeze every case for that merchant",
        scope=StopScope.MERCHANT,
        sla=timedelta(minutes=5),
        terminal_state=None,
        suppresses_contact=True,
        why="the merchant's customers are theirs; a pause is not a suggestion",
    ),
    "REG_HOLD": StopRule(
        code="REG_HOLD",
        trigger="a regulator or PSP advisory flag lands on the mandate",
        action="freeze the case and route it to human review",
        scope=StopScope.CASE,
        sla=timedelta(minutes=5),
        terminal_state=CaseState.HELD_FOR_HUMAN,
        suppresses_contact=True,
        why="an advisory is a signal from someone with more authority than this system",
    ),
}


def rule(code: str) -> StopRule:
    """Look up a stop rule, refusing anything not in the table.

    A stop code that does not exist is a programming error, not a runtime
    condition — silently accepting one would let a typo disable a stop.
    """
    try:
        return RULES[code]
    except KeyError:
        raise KeyError(
            f"unknown stop rule {code!r}; known codes: {', '.join(sorted(RULES))}"
        ) from None


def audit_code(code: str) -> str:
    """The form the audit trail records, per the specification."""
    return f"STOP_RULE:{rule(code).code}"
