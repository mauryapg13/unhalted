"""The types that move through the pipeline.

`FailureSignal` is the normalised form every source produces — a Razorpay
`payment.failed` webhook today, a `subscription.pending` webhook if that product
is ever entitled on the account, or a replayed captured payload. Nothing
downstream of normalisation knows or cares which source it came from.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
        specific numbers are asserted. C8 makes them answerable: the confidence
        above which auto-executing turned out right more often than holding
        would have been. See issue #7.
        """
        if self.confidence >= 0.90:
            return "auto-execute"
        if self.confidence >= 0.70:
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
    outcome: str | None = None
