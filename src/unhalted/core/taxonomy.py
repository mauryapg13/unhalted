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
    """How many distinct root causes Razorpay documents for this reason.

    When the method is known and documented, its own count is the answer —
    ambiguity is method-specific, which is the whole reason method is in the
    key. `payment_timed_out` has one documented cause on cards and two on UPI.

    When the method is absent, or is one Razorpay publishes no root-cause table
    for, the honest answer is the **worst** count across the methods that do
    document it. Falling through to the method-agnostic list instead — which
    records one cause for everything, because it has no method to attribute to —
    read the absence of a table as evidence of certainty, and made an unknown
    method score higher than a known one. Knowing less must never buy more
    autonomy.
    """
    reasons = _data()["reasons"]

    entry = reasons.get(method or "", {}).get(reason)
    if entry:
        return entry["causes"], entry.get("cause_names", [])

    documented = [
        data
        for scope, rs in reasons.items()
        if scope != "any"
        for r, data in rs.items()
        if r == reason
    ]
    if documented:
        worst = max(documented, key=lambda d: d["causes"])
        return worst["causes"], worst.get("cause_names", [])

    fallback = reasons.get("any", {}).get(reason)
    if fallback:
        return fallback["causes"], fallback.get("cause_names", [])
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
    # INFERRED, not DIRECT. Razorpay's own description of `payment_failed` says
    # "declined due to business or technical reasons. The exact reason in this
    # case is not communicated to Razorpay." A source narrows *who* declined,
    # never *why* — and "business or technical" spans a dead mandate and plain
    # downtime, which want opposite responses. Marking this DIRECT claimed their
    # documentation stated a class it explicitly declines to state.
    (ANY, "payment_failed", "bank", ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        INFERRED,
        "bank-side decline with no cause communicated to Razorpay; technical is the "
        "likelier of the two they name, not the stated one",
    ),
    (ANY, "payment_failed", "issuer", ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        INFERRED,
        "issuer-side decline with no cause communicated to Razorpay",
    ),
    # Their emandate reference emits `issuer_bank`, not `issuer`. Keyed on the
    # value they document rather than the one we guessed.
    (ANY, "payment_failed", "issuer_bank", ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        INFERRED,
        "issuer-side decline with no cause communicated to Razorpay",
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
    # ---------------------------------------------------------------------
    # Emandate subsequent payments. Razorpay documents these in
    # `payments/recurring-payments/emandate/errors.md`, which the taxonomy
    # generator did not read until now — so a mandate debit failing, the exact
    # event this product exists for, had eight reasons with no rule at all.
    # ---------------------------------------------------------------------
    # Their "Next Steps" column says re-register the mandate. That is
    # mandate-state-broken by definition: no retry reaches a mandate that is
    # gone, and re-authorisation is the only path.
    (ANY, "mandate_not_active", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the registered mandate is no longer active; the customer or bank "
        "cancelled it, and they say the customer must re-register",
    ),
    (ANY, "bank_account_invalid", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the account is closed or no longer valid; they say the customer "
        "must re-register for the mandate",
    ),
    (ANY, "incorrect_ifsc", ANY, ANY): Rule(
        DiagnosisClass.MANDATE_STATE_BROKEN,
        DIRECT,
        "Razorpay: the bank IFSC is no longer valid; they say the customer must "
        "re-register for the mandate",
    ),
    # Distinct from `mandate_not_active` in the one way that matters: the
    # mandate exists and the bank has simply not finished activating it. Their
    # next step is to wait and retry, so a retry is the correct response and
    # re-authorisation would be actively wrong.
    (ANY, "payment_mandate_not_active", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        DIRECT,
        "Razorpay: the mandate is not yet activated at the bank and banks sometimes "
        "take longer; they say to retry after some time",
    ),
    (ANY, "bank_account_validation_failed", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        INFERRED,
        "Razorpay: the bank could not validate the registration for debiting; they "
        "say to retry, so it is treated as transient rather than terminal",
    ),
    (ANY, "server_error", ANY, ANY): Rule(
        DiagnosisClass.RECOVERABLE_TECHNICAL,
        DIRECT,
        "Razorpay: a technical error at their own server; nothing about the customer",
    ),
    # Deliberately held, like payment_risk_check_failed. Razorpay attributes
    # both of these to the *merchant's* request, not to the customer or their
    # bank. Every recovery class would send a message to a customer who has done
    # nothing wrong about a bug on our side. The correct action is to stop and
    # tell a person to fix the integration, and UNKNOWN routes there.
    (ANY, "invalid_amount", ANY, ANY): Rule(
        DiagnosisClass.UNKNOWN,
        0.0,
        "Razorpay: the amount or currency in the payment request is invalid. A "
        "merchant-side integration fault, deliberately held rather than classified "
        "— no customer should be contacted about it",
    ),
    (ANY, "input_validation_failed", ANY, ANY): Rule(
        DiagnosisClass.UNKNOWN,
        0.0,
        "Razorpay: the payment request itself was wrong. A merchant-side integration "
        "fault, deliberately held rather than classified",
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
