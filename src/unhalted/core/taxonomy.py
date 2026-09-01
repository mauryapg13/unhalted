"""Failure classification, keyed on what Razorpay actually tells us.

The table is keyed on `(method, error_reason, error_source, error_step)`. Method
is in the key because ambiguity is method-specific: Razorpay documents one root
cause for `payment_timed_out` on cards and two on UPI, so the same reason is
determined in one method and undetermined in the other.

Confidence is derived, not chosen
---------------------------------
Two components, and each is separately checkable.

**Documented ambiguity** comes from `taxonomy_data.json`, generated from
Razorpay's own error references and pinned to a commit of their docs. If they
document `n` root causes for a reason, the reason alone cannot distinguish
between them, so it caps confidence at `1/n`. When `error_source` or
`error_step` select one of those causes, the ambiguity is resolved and the cap
lifts.

**Mapping directness** is ours, and it takes one of two values so it cannot be
tuned into false precision:

- `DIRECT` — Razorpay's own description states the class. "did not have enough
  funds to complete the transaction" is a balance failure in their words.
- `INFERRED` — we are reading between the lines. "the funds could not be
  debited" is *probably* balance, and probably is not certainly.

The product of the two is the confidence, and the audit record carries the
reasoning that produced it. Anyone can check the first component against
Razorpay's documentation and argue with the second on its merits. Neither is a
number someone picked because it looked plausible.
"""

from __future__ import annotations

import functools
import json
import pathlib
from typing import Any, NamedTuple

from unhalted.models import DiagnosisClass

DATA_FILE = pathlib.Path(__file__).parent / "taxonomy_data.json"

#: Matches any value at that position in the key.
ANY = "*"

#: How directly Razorpay's own description supports the class we assign.
DIRECT = 1.0
INFERRED = 0.8


class Rule(NamedTuple):
    klass: DiagnosisClass
    directness: float
    rationale: str


class Match(NamedTuple):
    rule: Rule
    key: str
    confidence: float
    reasoning: str


@functools.lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"{DATA_FILE} is missing. Generate it with:\n"
            "    uv run python scripts/build_taxonomy.py"
        )
    return json.loads(DATA_FILE.read_text())


def taxonomy_version() -> str:
    """The Razorpay docs commit this taxonomy's facts came from.

    Recorded on every diagnosis, so a classification can be replayed against the
    exact documentation that produced it.
    """
    return f"razorpay-docs@{_data()['generated_from']['commit'][:12]}"


def documented_causes(method: str | None, reason: str) -> tuple[int, list[str]]:
    """How many distinct root causes Razorpay documents for this reason."""
    reasons = _data()["reasons"]
    for scope in (method or "", "any"):
        entry = reasons.get(scope, {}).get(reason)
        if entry:
            return entry["causes"], entry.get("cause_names", [])
    return 1, []


#: (method, error_reason, error_source, error_step) -> Rule.
#: Most entries wildcard everything but the reason. A concrete `error_source`
#: appears only where it resolves a documented ambiguity.
TABLE: dict[tuple[str, str, str, str], Rule] = {
    (ANY, "insufficient_funds", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_BALANCE,
        DIRECT,
        "Razorpay: the account did not have enough funds",
    ),
    (ANY, "insufficient_fund", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_BALANCE,
        DIRECT,
        "card-flow spelling of insufficient balance",
    ),
    (ANY, "payment_declined", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_BALANCE,
        INFERRED,
        "Razorpay: funds could not be debited; balance is the likeliest cause but not stated",
    ),
    (ANY, "bank_technical_error", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        DIRECT,
        "Razorpay: downtime at the provider",
    ),
    # payment_failed carries no cause detail of its own; source is what says
    # whose problem it was.
    (ANY, "payment_failed", "bank", ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        DIRECT,
        "bank-side failure at authorisation; no customer action implied",
    ),
    (ANY, "payment_failed", "issuer", ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        DIRECT,
        "issuer-side failure at authorisation; no customer action implied",
    ),
    # The four documented ambiguities. A concrete source resolves each of them;
    # without one, the reason alone cannot choose, and the cap holds.
    (ANY, "payment_timed_out", "customer", ANY): Rule(
        DiagnosisClass.NOTIFICATION_GAP,
        DIRECT,
        "Razorpay: the customer exceeded the payment time limit",
    ),
    (ANY, "payment_timed_out", "bank", ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        DIRECT,
        "Razorpay: partner bank downtime, not the customer's doing",
    ),
    (ANY, "payment_timed_out", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        INFERRED,
        "timed out with no source given; treated as technical pending verification",
    ),
    (ANY, "gateway_technical_error", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        DIRECT,
        "Razorpay: partner bank technical issue or downtime; either way not the customer",
    ),
    (ANY, "credit_failed", "customer", ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: a bank account other than the registered one was used",
    ),
    (ANY, "credit_failed", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        INFERRED,
        "credit failed with no source given; partner bank downtime is the other documented cause",
    ),
    (ANY, "payment_cancelled", ANY, ANY): Rule(
        DiagnosisClass.CUSTOMER_INTENT_REVOKED,
        INFERRED,
        "Razorpay: the customer cancelled, though bank downtime is also documented",
    ),
    (ANY, "invalid_vpa", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the customer is not a valid user on the UPI app",
    ),
    (ANY, "transaction_on_vpa_restricted", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the VPA is blocked",
    ),
    (ANY, "card_expired", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the card has expired; a retry cannot fix it",
    ),
    (ANY, "debit_instrument_blocked", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the card is blocked by the issuer or the customer",
    ),
    (ANY, "card_disabled_for_online_payments", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the card is disabled for online payments",
    ),
    (ANY, "authentication_failed", ANY, ANY): Rule(
        DiagnosisClass.NOTIFICATION_GAP,
        INFERRED,
        "Razorpay: OTP or 3DS failed; the customer was present and did not complete it",
    ),
    (ANY, "card_not_enrolled", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the card is not enabled for online transactions; a retry cannot fix it",
    ),
    (ANY, "debit_instrument_inactive", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the card is not activated for online transactions",
    ),
    (ANY, "vpa_resolution_failed", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the customer's UPI ID could not be resolved",
    ),
    (ANY, "incorrect_cvv", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        INFERRED,
        "Razorpay: an incorrect CVV was entered; the instrument needs re-authorisation, "
        "and no CVV is entered on a stored-token debit",
    ),
    (ANY, "transaction_limit_exceeded", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_BALANCE,
        INFERRED,
        "Razorpay: the card's daily transaction limit is reached; not a balance failure, "
        "but the same recovery path — wait and retry",
    ),
    (ANY, "card_declined", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        INFERRED,
        "Razorpay: declined by the customer's bank, without a stated cause; "
        "worth one retry, and nothing more can be read into it",
    ),
    (ANY, "payment_failed", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        INFERRED,
        "Razorpay: declined by the customer's bank, without a stated cause",
    ),
    # Deliberately held, not unrecognised. Razorpay says the bank declined this
    # citing fraud. None of the six recovery classes fit: mandate-state-broken
    # would route it to re-authorisation, which is precisely the wrong response
    # to a fraud flag. The honest action is to stop and involve a person, and
    # UNKNOWN is what routes there. The rationale below is what distinguishes
    # this from a reason we simply do not recognise.
    (ANY, "payment_risk_check_failed", ANY, ANY): Rule(
        DiagnosisClass.UNKNOWN,
        0.0,
        "Razorpay: the bank declined this as fraudulent. Deliberately held for a human "
        "rather than classified — no automated recovery path is appropriate",
    ),
    (ANY, "payment_collect_request_expired", ANY, ANY): Rule(
        DiagnosisClass.NOTIFICATION_GAP,
        DIRECT,
        "Razorpay: the customer did not act within the collect window",
    ),
}

#: Applied when nothing matches. Never guesses.
UNMATCHED = Rule(
    DiagnosisClass.UNKNOWN,
    0.0,
    "no taxonomy entry for this combination of method, reason, source and step",
)


def lookup(
    method: str | None,
    reason: str | None,
    source: str | None,
    step: str | None,
) -> Match:
    """Classify a failure and derive how much the signal actually determined."""
    if not reason:
        return Match(UNMATCHED, "", 0.0, "no error_reason on the payment")

    candidates = (
        (method or ANY, reason, source or ANY, step or ANY),
        (method or ANY, reason, source or ANY, ANY),
        (ANY, reason, source or ANY, step or ANY),
        (ANY, reason, source or ANY, ANY),
        (ANY, reason, ANY, ANY),
    )

    for key in candidates:
        rule = TABLE.get(key)
        if rule is None:
            continue

        n, cause_names = documented_causes(method, reason)
        source_is_concrete = key[2] != ANY
        resolved = n == 1 or source_is_concrete

        cap = 1.0 if resolved else 1.0 / n
        confidence = round(cap * rule.directness, 2)

        if n == 1:
            why = f"Razorpay documents one root cause for {reason}"
        elif source_is_concrete:
            why = (
                f"{reason} has {n} documented causes ({', '.join(cause_names)}); "
                f"error_source={key[2]} selects one"
            )
        else:
            why = (
                f"{reason} has {n} documented causes ({', '.join(cause_names)}) "
                "and no error_source was given, so none is excluded"
            )
        if rule.directness < DIRECT:
            why += "; the class is inferred rather than stated"

        return Match(rule, "|".join(key), confidence, f"{why}. {rule.rationale}")

    return Match(UNMATCHED, "", 0.0, UNMATCHED.rationale)
