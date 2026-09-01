"""The pipeline, run against payments Razorpay actually produced.

These are not examples from documentation and not objects written by hand. Each
is a real test-mode payment that failed at Razorpay's hosted checkout, arrived
here over a real webhook with a verified signature, and was fetched back through
their API. `tests/fixtures/razorpay/captured/` records the payment id and
capture date of each.

If the directory is empty the tests skip rather than pass. See
docs/capturing-fixtures.md for how to fill it.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from unhalted.core.diagnose import diagnose
from unhalted.ingest.normalize import from_payment_failed
from unhalted.models import DiagnosisClass, DiagnosisSource

CAPTURED = pathlib.Path(__file__).parent / "fixtures" / "razorpay" / "captured"
FILES = sorted(CAPTURED.glob("*.json"))

pytestmark = pytest.mark.skipif(
    not FILES, reason="no captured payments yet; see docs/capturing-fixtures.md"
)


def as_event(record: dict) -> dict:
    """Wrap a captured payment in the webhook envelope Razorpay sends."""
    payment = record["payment"]
    return {
        "entity": "event",
        "event": "payment.failed",
        "payload": {"payment": {"entity": payment}},
        "created_at": payment.get("created_at"),
    }


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem[-12:])
def test_a_real_payment_normalises_without_loss(path: pathlib.Path) -> None:
    record = load(path)
    signal = from_payment_failed(as_event(record))
    payment = record["payment"]

    assert signal.payment_id == payment["id"]
    assert signal.amount_paise == payment["amount"]
    assert signal.method == payment["method"]
    assert signal.error_reason == payment["error_reason"]
    assert signal.error_source == payment["error_source"]
    assert signal.error_step == payment["error_step"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem[-12:])
def test_a_real_payment_is_diagnosed_from_the_generated_taxonomy(
    path: pathlib.Path,
) -> None:
    signal = from_payment_failed(as_event(load(path)))
    d = diagnose(signal)

    assert d.source is DiagnosisSource.RULES_TABLE
    assert d.taxonomy_version.startswith("razorpay-docs@")
    assert d.klass is not DiagnosisClass.UNKNOWN, (
        f"{signal.error_reason}/{signal.error_source} is a reason Razorpay really "
        "produced and the taxonomy does not cover it"
    )
    assert 0.0 < d.confidence <= 1.0


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem[-12:])
def test_no_raw_contact_details_survive_normalisation(path: pathlib.Path) -> None:
    """The customer reference must be a digest, never the contact itself."""
    record = load(path)
    signal = from_payment_failed(as_event(record))
    payment = record["payment"]

    assert signal.customer_ref.startswith("cust_")
    for field in ("contact", "email", "vpa"):
        value = payment.get(field)
        if value:
            assert value not in signal.customer_ref


def test_every_captured_payment_records_where_it_came_from() -> None:
    for path in FILES:
        record = load(path)
        assert record["captured_at"], f"{path.name} has no capture date"
        assert record["captured_from"], f"{path.name} has no provenance"
        assert record["payment"]["id"].startswith("pay_")
