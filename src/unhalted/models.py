"""The types that move through the pipeline.

`FailureSignal` is the normalised form every source produces — a Razorpay
`payment.failed` webhook today, a `subscription.pending` webhook if that product
is ever entitled on the account, or a replayed captured payload. Nothing
downstream of normalisation knows or cares which source it came from.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from unhalted.policy import POLICY


class DiagnosisClass(str, enum.Enum):
    """Root-cause classes. Each maps to a distinct recovery path."""

    RECOVERABLE_TECHNICAL = "recoverable-technical"
    RECOVERABLE_BALANCE = "recoverable-balance"
    NOTIFICATION_GAP = "notification-gap"
    MANDATE_STATE_BROKEN = "mandate-state-broken"
    CUSTOMER_INTENT_REVOKED = "customer-intent-revoked"
    UNKNOWN = "unknown"


class DiagnosisSource(str, enum.Enum):
    RULES_TABLE = "rules-table"
    MODEL = "model"


class Intent(str, enum.Enum):
    """What a customer reply is doing.

    A closed set. The model chooses from these or returns `unknown`; it cannot
    invent an intent, because every one of these has a coded consequence and an
    intent with no consequence would be a silent no-op.
    """

    PROMISE_TO_PAY = "promise-to-pay"
    DISPUTE = "dispute"
    SET_OFF_REQUEST = "set-off-request"
    OPT_OUT = "opt-out"
    CANCELLATION_REQUEST = "cancellation-request"
    SERVICE_COMPLAINT = "service-complaint"
    DISTRESS = "distress"
    CHANNEL_PREFERENCE = "channel-preference"
    UNKNOWN = "unknown"


class Sentiment(str, enum.Enum):
    COOPERATIVE = "cooperative"
    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"
    DISTRESS = "distress"


class CaseState(str, enum.Enum):
    OPEN = "open"
    HELD_FOR_HUMAN = "held-for-human"
    RECOVERED = "recovered"
    UNRECOVERED = "unrecovered"
    CLOSED_REVOKED = "closed-revoked"
    CLOSED_FALSE_FAILURE = "closed-false-failure"


class FailureSignal(BaseModel):
    """A failed debit, normalised away from whichever API reported it."""

    payment_id: str
    order_id: str | None = None
    customer_ref: str
    amount_paise: int
    currency: str = "INR"
    method: str | None = None
    error_code: str | None = None
    error_reason: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    occurred_at: datetime
    token_id: str | None = None
    mandate_max_paise: int | None = Field(
        default=None,
        description=(
            "The ceiling the customer agreed to when the mandate was created. "
            "None when we have not fetched the token; the network ceilings still "
            "apply, but the consent check cannot run and says so."
        ),
    )
    source: str = Field(description="Which ingest adapter produced this signal")
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def amount_rupees(self) -> float:
        return self.amount_paise / 100


class Diagnosis(BaseModel):
    """A classification, and enough provenance to replay how it was reached."""

    klass: DiagnosisClass
    confidence: float
    source: DiagnosisSource
    reasoning: str
    taxonomy_version: str
    model_name: str | None = None
    prompt_hash: str | None = None

    @property
    def authority(self) -> str:
        """Confidence decides how much autonomy a diagnosis earns.

        The two cut-points below are **policy, not measurement**. They came from
        the specification; nobody measured them. A case at 0.69 holds for a human
        and one at 0.71 acts, and that boundary is as chosen as the confidence
        values the taxonomy stopped inventing.

        The ordering is defensible — less certain means less autonomy — but the
        specific numbers are asserted, in config/policy.yaml, not here. C8 makes
        them answerable: the confidence above which auto-executing turned out
        right more often than holding would have been. See issue #7.
        """
        if self.confidence >= POLICY.confidence_auto_execute:
            return "auto-execute"
        if self.confidence >= POLICY.confidence_sampled_qa:
            return "auto-execute-sampled-qa"
        return "hold-for-human"


class Case(BaseModel):
    """One failed debit being worked. The unit the agent reasons about."""

    id: str
    customer_ref: str
    amount_paise: int
    state: CaseState = CaseState.OPEN
    opened_at: datetime
    retry_count: int = 0
    #: A date the customer named for payment. No nudge fires before it —
    #: the shell has always computed this from a promise-to-pay reply, and
    #: until it was persisted here nothing ever read it back.
    nudges_suspended_until: date | None = None

    @property
    def amount_rupees(self) -> float:
        return self.amount_paise / 100


class AuditRecord(BaseModel):
    """One decision, attributable and replayable.

    Every decision the agent commits writes one of these. Together they are the
    case timeline, and they are the only account of what happened that anyone
    should trust.
    """

    case_id: str
    at: datetime
    decision_type: str
    action: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    rules_fired: list[str] = Field(default_factory=list)
    rule_version: str | None = None
    model_name: str | None = None
    confidence: float | None = None
    #: Who decided, when a person did. The specification requires this on every
    #: human gate, because a decision nobody is named for is a decision nobody
    #: is answerable for.
    human_actor: str | None = None
    outcome: str | None = None


class DetectedIntent(BaseModel):
    """One intent found in a reply, with the words that justify it."""

    type: Intent
    confidence: float
    #: The span of the reply supporting this intent, quoted. Required, because a
    #: reviewer needs to see why — and because a model asked to quote its
    #: evidence asserts less that the text does not say.
    evidence: str = ""


class ParsedReply(BaseModel):
    """A customer reply, read into structure. Nothing here has been acted on.

    `payment_date_raw` is deliberately a string. The model proposes a date; the
    shell decides whether it is one. A model that emits "31st February" must be
    refused by code that checks, not accepted by a type that happened to parse.
    """

    raw: str
    language: str = "unknown"
    intents: list[DetectedIntent] = Field(default_factory=list)
    payment_date_raw: str | None = None
    condition: str | None = None
    sentiment: Sentiment = Sentiment.NEUTRAL
    model_name: str | None = None
    prompt_hash: str | None = None
    #: How many model calls this parse took. More than one means the endpoint
    #: returned nothing usable and was retried — a reliability fact, distinct
    #: from whether the reading was right.
    attempts: int = 0
    #: What those calls cost, from the provider's own reported `usage.cost`.
    #: Failed parses carry a cost too: a call that returns nothing is still
    #: billed, and a spend figure that omits them understates what the model
    #: costs to run.
    cost_usd: float = 0.0
    #: Set when the model could not be reached or returned nothing usable. The
    #: reply is preserved and queued; no intent is inferred from silence.
    failed: bool = False
    failure_reason: str | None = None

    def confidence_for(self, intent: Intent) -> float:
        return max((i.confidence for i in self.intents if i.type is intent), default=0.0)

    def has(self, intent: Intent, *, at_least: float = 0.0) -> bool:
        return self.confidence_for(intent) >= at_least and any(
            i.type is intent for i in self.intents
        )
