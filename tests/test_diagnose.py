"""Diagnosis from Razorpay's real error fields."""

from __future__ import annotations

from datetime import datetime

import pytest

from unhalted.core.diagnose import diagnose
from unhalted.models import DiagnosisClass, DiagnosisSource, FailureSignal
from unhalted.shell.windows import IST


def signal(**overrides: object) -> FailureSignal:
    base = {
        "payment_id": "pay_TEST0001",
        "customer_ref": "cust_001",
        "amount_paise": 49900,
        "occurred_at": datetime(2026, 9, 1, 10, 42, tzinfo=IST),
        "source": "test",
    }
    base.update(overrides)
    return FailureSignal(**base)  # type: ignore[arg-type]


def test_insufficient_funds_resolves_without_a_model() -> None:
    d = diagnose(signal(error_reason="insufficient_funds", error_source="customer"))
    assert d.klass is DiagnosisClass.RECOVERABLE_BALANCE
    assert d.source is DiagnosisSource.RULES_TABLE
    assert d.confidence >= 0.95
    assert d.authority == "auto-execute"


def test_card_flow_spelling_is_also_recognised() -> None:
    """Razorpay uses `insufficient_fund` on cards and `insufficient_funds` on UPI."""
    d = diagnose(signal(error_reason="insufficient_fund", error_source="issuer_bank"))
    assert d.klass is DiagnosisClass.RECOVERABLE_BALANCE


def test_novel_reason_is_held_not_guessed() -> None:
    d = diagnose(signal(error_reason="DECLINED - AP RULE 7A"))
    assert d.klass is DiagnosisClass.UNKNOWN
    assert d.confidence == 0.0
    assert d.authority == "hold-for-human"


def test_missing_reason_is_held() -> None:
    assert diagnose(signal()).klass is DiagnosisClass.UNKNOWN


def test_diagnosis_records_what_matched_it() -> None:
    d = diagnose(signal(error_reason="insufficient_funds", error_source="customer"))
    assert "insufficient_funds" in d.reasoning
    assert d.taxonomy_version


def test_low_confidence_reason_does_not_earn_full_autonomy() -> None:
    d = diagnose(signal(error_reason="payment_declined"))
    assert d.klass is DiagnosisClass.RECOVERABLE_BALANCE
    assert d.authority == "auto-execute-sampled-qa"


# --- Findings from the exploratory pass (#22-#29) -------------------------


def _sig(**overrides: object) -> FailureSignal:
    """The shared helper, defaulted to emandate — the recurring debit case."""
    fields: dict = {"method": "emandate", "error_source": None}
    fields.update(overrides)
    return signal(**fields)


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("mandate_not_active", DiagnosisClass.MANDATE_STATE_BROKEN),
        ("bank_account_invalid", DiagnosisClass.MANDATE_STATE_BROKEN),
        ("incorrect_ifsc", DiagnosisClass.MANDATE_STATE_BROKEN),
        ("payment_mandate_not_active", DiagnosisClass.RECOVERABLE_TECHNICAL),
        ("bank_account_validation_failed", DiagnosisClass.RECOVERABLE_TECHNICAL),
        ("server_error", DiagnosisClass.RECOVERABLE_TECHNICAL),
    ],
)
def test_emandate_subsequent_payment_reasons_are_classified(reason, expected) -> None:
    """Razorpay's emandate table is the one document this product is about."""
    assert diagnose(_sig(error_reason=reason)).klass is expected


def test_a_mandate_that_is_dead_and_one_not_yet_activated_are_not_the_same() -> None:
    """One needs re-registration; the other genuinely just needs waiting."""
    dead = diagnose(_sig(error_reason="mandate_not_active"))
    pending = diagnose(_sig(error_reason="payment_mandate_not_active"))
    assert dead.klass is DiagnosisClass.MANDATE_STATE_BROKEN
    assert pending.klass is DiagnosisClass.RECOVERABLE_TECHNICAL


@pytest.mark.parametrize("reason", ["invalid_amount", "input_validation_failed"])
def test_a_merchant_integration_fault_is_held_not_chased(reason) -> None:
    """Razorpay attributes these to our request. No customer should hear about it."""
    d = diagnose(_sig(error_reason=reason))
    assert d.klass is DiagnosisClass.UNKNOWN
    assert d.authority == "hold-for-human"
    assert "merchant" in d.reasoning.lower()


@pytest.mark.parametrize("method", ["netbanking", "wallet", None])
def test_an_unknown_method_never_scores_above_a_known_ambiguous_one(method) -> None:
    """Knowing less must not buy more autonomy. Issue #23."""
    unknown = diagnose(_sig(method=method, error_reason="payment_timed_out"))
    upi = diagnose(_sig(method="upi", error_reason="payment_timed_out"))
    assert unknown.confidence <= upi.confidence
    assert unknown.authority == "hold-for-human"


@pytest.mark.parametrize("source", ["bank", "issuer", "issuer_bank", "gateway", None])
def test_payment_failed_never_reaches_full_confidence(source) -> None:
    """Razorpay says the exact reason is not communicated to them. Issue #29."""
    d = diagnose(_sig(error_reason="payment_failed", error_source=source))
    assert d.confidence < 1.0, f"{source} claimed certainty Razorpay does not offer"
    assert d.authority != "auto-execute"
