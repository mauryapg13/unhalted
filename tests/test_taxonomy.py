"""The taxonomy's facts come from Razorpay; its confidences are derived from them.

These tests assert that the confidence a diagnosis carries is a function of what
Razorpay documents, not a number anyone chose. If they pass while
`taxonomy_data.json` is stale, the drift check in CI is what catches that.
"""

from __future__ import annotations

import json

from unhalted.core.taxonomy import (
    DATA_FILE,
    documented_causes,
    lookup,
    taxonomy_version,
)
from unhalted.models import DiagnosisClass

# -- the facts, straight from Razorpay's references ---------------------------


def test_the_data_is_pinned_to_a_razorpay_docs_commit() -> None:
    data = json.loads(DATA_FILE.read_text())
    origin = data["generated_from"]
    assert origin["repo"] == "razorpay/markdown-docs"
    assert len(origin["commit"]) == 40
    assert taxonomy_version().startswith("razorpay-docs@")


def test_exactly_the_documented_ambiguities_are_recorded() -> None:
    """Four reason/method pairs have more than one documented root cause.

    Checked by reading Razorpay's cards.md and upi.md. If they document another,
    this fails and the confidence for that reason should drop accordingly.
    """
    data = json.loads(DATA_FILE.read_text())
    ambiguous = {
        f"{method}:{reason}"
        for method, reasons in data["reasons"].items()
        for reason, entry in reasons.items()
        if entry["causes"] > 1
    }
    assert ambiguous == {
        "card:payment_cancelled",
        "upi:credit_failed",
        "upi:gateway_technical_error",
        "upi:payment_timed_out",
    }


def test_cause_counts_are_method_specific() -> None:
    """The finding the whole four-part key exists for."""
    assert documented_causes("card", "payment_timed_out")[0] == 1
    assert documented_causes("upi", "payment_timed_out")[0] == 2


# -- confidence is derived ----------------------------------------------------


def test_one_documented_cause_and_a_stated_class_gives_full_confidence() -> None:
    m = lookup("upi", "insufficient_funds", "customer", "payment_authorization")
    assert m.rule.klass is DiagnosisClass.RECOVERABLE_BALANCE
    assert m.confidence == 1.0


def test_an_inferred_class_never_reaches_full_confidence() -> None:
    """payment_declined says funds could not be debited, not that balance was short."""
    m = lookup("card", "payment_declined", None, None)
    assert m.confidence == 0.8
    assert "inferred" in m.reasoning


def test_an_unresolved_ambiguity_is_capped_at_one_over_n() -> None:
    m = lookup("upi", "payment_timed_out", None, None)
    assert m.confidence == 0.4  # two documented causes, inferred class
    assert "2 documented causes" in m.reasoning
    assert "no error_source" in m.reasoning


def test_a_concrete_source_resolves_the_ambiguity_and_lifts_the_cap() -> None:
    customer = lookup("upi", "payment_timed_out", "customer", None)
    bank = lookup("upi", "payment_timed_out", "bank", None)

    assert customer.confidence == 1.0
    assert bank.confidence == 1.0
    # And they resolve to different classes, which is the point.
    assert customer.rule.klass is DiagnosisClass.NOTIFICATION_GAP
    assert bank.rule.klass is DiagnosisClass.RECOVERABLE_TECHNICAL


def test_the_same_reason_is_more_certain_on_cards_than_on_upi() -> None:
    """Razorpay documents one cause on cards and two on UPI."""
    card = lookup("card", "payment_timed_out", None, None)
    upi = lookup("upi", "payment_timed_out", None, None)
    assert card.confidence > upi.confidence


def test_an_unresolved_ambiguity_falls_below_the_autonomy_threshold() -> None:
    """0.70 is where the specification stops allowing autonomous action."""
    assert lookup("upi", "payment_timed_out", None, None).confidence < 0.70
    assert lookup("upi", "credit_failed", None, None).confidence < 0.70
    assert lookup("card", "payment_cancelled", None, None).confidence < 0.70


def test_a_novel_reason_gets_no_confidence_at_all() -> None:
    m = lookup("upi", "DECLINED - AP RULE 7A", None, None)
    assert m.rule.klass is DiagnosisClass.UNKNOWN
    assert m.confidence == 0.0


def test_a_missing_reason_is_not_guessed_at() -> None:
    assert lookup("upi", None, "customer", None).rule.klass is DiagnosisClass.UNKNOWN


# -- the reasoning is checkable ----------------------------------------------


def test_the_reasoning_names_the_documented_causes_it_weighed() -> None:
    """An auditor should be able to check this sentence against Razorpay's docs."""
    m = lookup("upi", "credit_failed", None, None)
    assert "Customer Bank Account Mismatch" in m.reasoning
    assert "Partner Bank Downtime" in m.reasoning


# -- coverage, which is C3's actual bar ---------------------------------------


def _classified(method: str, reason: str) -> tuple[bool, bool]:
    """(mapped at all, deliberately held)."""
    m = lookup(method, reason, None, None)
    held = m.rule.klass is DiagnosisClass.UNKNOWN
    return bool(m.key), held


def test_every_documented_card_and_upi_reason_is_accounted_for() -> None:
    """No documented reason may fall through to 'we have never heard of this'."""
    data = json.loads(DATA_FILE.read_text())
    unmapped = [
        f"{method}:{reason}"
        for method in ("card", "upi")
        for reason in data["reasons"][method]
        if not _classified(method, reason)[0]
    ]
    assert unmapped == [], f"documented reasons with no taxonomy entry: {unmapped}"


def test_only_the_fraud_decline_is_deliberately_held() -> None:
    """Held is a decision, and it should be a rare and stated one.

    A bank calling a payment fraudulent has no appropriate automated recovery —
    re-authorisation, the nearest class, is exactly the wrong response.
    """
    data = json.loads(DATA_FILE.read_text())
    held = [
        f"{method}:{reason}"
        for method in ("card", "upi")
        for reason in data["reasons"][method]
        if _classified(method, reason)[1]
    ]
    assert held == ["card:payment_risk_check_failed"]

    m = lookup("card", "payment_risk_check_failed", None, None)
    assert "fraudulent" in m.rule.rationale
    assert m.key, "a held reason must still record that it matched a rule"


# -- the source can be the whole answer ---------------------------------------


def test_a_merchant_fault_is_held_however_permissive_the_reason_is() -> None:
    """Razorpay's failure-analysis guide, on business failures: "Business
    failures require corrective action rather than retries. These issues stem
    from merchant-side configuration or account settings — simply retrying the
    same request won't resolve them."

    `payment_failed` carries a permissive rule because a bank decline is worth
    one retry. The same reason arriving with `error_source: business` is a
    broken integration that will fail identically every time, and matching it
    against the reason alone scheduled exactly the retry their guidance says
    cannot work.
    """
    bank = lookup("card", "payment_failed", "bank", None)
    business = lookup("card", "payment_failed", "business", None)

    assert bank.rule.klass is DiagnosisClass.RECOVERABLE_TECHNICAL
    assert business.rule.klass is DiagnosisClass.UNKNOWN
    assert business.confidence == 0.0, "held for a person, not acted on"
    assert "retrying cannot resolve it" in business.rule.rationale


def test_a_merchant_fault_does_not_read_as_a_gap_in_the_table() -> None:
    """`scripts/propose_taxonomy_rule.py` clusters held cases on the phrase
    "no taxonomy entry" to find reasons still needing a rule. A merchant fault
    is a classification, not a gap, and must not be filed as work to do."""
    business = lookup("card", "payment_failed", "business", None)
    assert "no taxonomy entry" not in business.rule.rationale
    assert business.key, "a held source must still record what it matched on"

    novel = lookup("card", "something_nobody_has_seen", "gateway", None)
    assert "no taxonomy entry" in novel.rule.rationale, "a real gap still reads as one"


def test_every_business_failure_razorpay_documents_has_a_rule() -> None:
    """The four their failure-analysis guide names. Two were already mapped;
    the other two landed on UNKNOWN through the unmatched fallback, so nothing
    was ever retried on them — but they read as gaps rather than decisions."""
    for reason in (
        "input_validation_failed",
        "international_transaction_not_allowed",
        "invalid_amount",
        "invalid_currency",
    ):
        m = lookup("card", reason, None, None)
        assert m.rule.klass is DiagnosisClass.UNKNOWN, reason
        assert "no taxonomy entry" not in m.rule.rationale, (
            f"{reason} is a documented merchant fault, not an unrecognised reason"
        )
