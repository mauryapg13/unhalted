"""Diagnosis from Razorpay's real error fields."""

from __future__ import annotations

from datetime import datetime

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
