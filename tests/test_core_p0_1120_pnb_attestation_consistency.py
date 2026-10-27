"""P0 #1120: never mistake a corrupt PNB return status for proven absence.

Tests use a synthetic Paris session and a real local SQLite checkpoint.
No ReturnDigitalLoan POST, ADEPT call, real inventory, or artifact mutation.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

import json

import pytest

from cifereca.core.loan_return_guard import (
    provider_absence_attested, public_return_state, return_key,
)
from tests.test_core_0982_loan_return_reconciliation import (
    _library, _loan, Session,
)


STAMP = "2026-10-19T05:42:00+00:00"
PRIVATE = "https://private.invalid/book.acsm?auth=NEVER_ECHO"


@pytest.mark.parametrize(("verification", "checked_at"), [
    ("still_present", STAMP),
    ("unavailable", STAMP),
    ("not_checked", STAMP),
    ("provider_absent", None),
    ("provider_absent", PRIVATE),
    (PRIVATE, STAMP),
    ({"token": PRIVATE}, STAMP),
])
def test_inconsistent_confirmed_record_never_claims_inventory_verified(
    verification, checked_at,
):
    record = {
        "status": "provider_return_confirmed",
        "verification": verification,
        "request_accepted": True,
        "requested_at": STAMP,
        "checked_at": checked_at,
        "operation_id": "synthetic-operation",
        "private_url": PRIVATE,
    }
    before = repr(record)
    assert provider_absence_attested(record) is False
    result = public_return_state(record)
    assert result["status"] == "verification_unavailable"
    assert result["verification"] == "unavailable"
    assert result["provider_inventory_verified"] is False
    assert result["retry"] == "verification_first"
    assert result["request_accepted"] is True
    assert "NEVER_ECHO" not in json.dumps(result)
    assert repr(record) == before


def test_crash_recovered_inventory_can_confirm_without_saved_post_ack():
    row = {
        "status": "provider_return_confirmed",
        "verification": "provider_absent",
        "request_accepted": False,
        "requested_at": STAMP,
        "checked_at": STAMP,
    }
    assert provider_absence_attested(row) is True
    public = public_return_state(row)
    assert public["status"] == "provider_return_confirmed"
    assert public["provider_inventory_verified"] is True
    assert public["request_accepted"] is False
    assert public["retry"] == "none"


@pytest.mark.parametrize("invalid", [
    ("still_present", STAMP),
    ("provider_absent", PRIVATE),
    ("unavailable", STAMP),
])
def test_conflicting_durable_success_rechecked_in_inventory_only(
    tmp_path, invalid,
):
    verification, checked_at = invalid
    session = Session([])
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    service = _library(tmp_path, session=session)
    loan = _loan()
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        claimed, row = service.storage.loan_return_claim(key, "synthetic-op")
        assert claimed
        row.update(
            status="provider_return_confirmed",
            verification=verification,
            checked_at=checked_at,
            request_accepted=True,
        )
        service.storage.metadata_set(key, json.dumps(row))
        public = service.loan_return_state(loan)
        assert public["status"] == "verification_unavailable"
        assert public["provider_inventory_verified"] is False
        assert service.storage.loan_return_state(key) == row
        fresh = service.verify_return_digital(loan)
        assert fresh["status"] == "provider_return_confirmed"
        assert fresh["verification"] == "provider_absent"
        assert fresh["provider_inventory_verified"] is True
        assert session.checks == ["linked-b"]
        assert session.posts == []
        assert service.storage.loan_return_state(key)["verification"] == "provider_absent"
        assert service.return_digital(loan)["provider_inventory_verified"] is True
        assert session.posts == []
    finally:
        service.close()


def test_confirmation_only_skips_repeat_inventory_if_evidence_is_consistent(tmp_path):
    session = Session([])
    service = _library(tmp_path, session=session)
    loan = _loan()
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        assert service.storage.loan_return_claim(key, "synthetic-op")[0]
        service.storage.loan_return_transition(
            key, "synthetic-op", status="provider_return_confirmed",
            verification="provider_absent", accepted=False,
        )
        status = service.verify_return_digital(loan)
        assert status["provider_inventory_verified"] is True
        assert session.checks == []
        assert session.posts == []
    finally:
        service.close()
