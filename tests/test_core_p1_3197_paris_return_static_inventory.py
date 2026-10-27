"""#3197: legacy mutable statuses never attest Paris loan returns.

Exercise the Core SQLite flow with in-memory providers, not HTTP or ADEPT.
Legacy positive loans remain observable; absence needs typed DILICOM coverage.
"""
from __future__ import annotations

import pytest

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete
from tests.test_core_0982_loan_return_reconciliation import (
    Session, _library, _loan,
)


@pytest.mark.parametrize("legacy_status", [
    [], [{"upstream": "SYRACUSE"}], [{"upstream": "DILICOM"}], None,
])
def test_empty_v1_never_attests_absence_or_replays_return(tmp_path, legacy_status):
    loan = _loan()
    session = Session([])
    session.last_loan_errors = legacy_status
    service = _library(tmp_path, session=session)
    try:
        first = service.return_digital(loan)
        assert first["status"] == "verification_unavailable"
        assert first["verification"] == "unavailable"
        assert first["provider_inventory_verified"] is False
        assert first["request_accepted"] is True
        assert session.posts == [(loan.id, "linked-b")]
        assert service.verify_return_digital(loan)["status"] == "verification_unavailable"
        assert session.posts == [(loan.id, "linked-b")]
        assert session.checks == ["linked-b", "linked-b"]
    finally:
        service.close()


def test_legacy_positive_presence_remains_observable(tmp_path):
    loan = _loan()
    session = Session([loan])
    service = _library(tmp_path, session=session)
    try:
        first = service.return_digital(loan)
        assert first["status"] == "still_active"
        assert first["verification"] == "still_present"
        assert session.posts == [(loan.id, "linked-b")]
        assert session.checks == ["linked-b"]
    finally:
        service.close()


def test_pending_legacy_return_requires_subsequent_typed_proof(tmp_path):
    loan = _loan()
    session = Session([])
    service = _library(tmp_path, session=session)
    try:
        assert service.return_digital(loan)["status"] == "verification_unavailable"
        session.last_loan_errors = []
        assert service.verify_return_digital(loan)["status"] == "verification_unavailable"
        attest_paris_dilicom_complete(session, credential_id="paris-a")
        result = service.verify_return_digital(loan)
        assert result["status"] == "provider_return_confirmed"
        assert result["verification"] == "provider_absent"
        assert result["provider_inventory_verified"] is True
        assert session.posts == [(loan.id, "linked-b")]
        assert session.checks == ["linked-b"] * 3
    finally:
        service.close()


def test_paris_return_static_reader_check_rejects_hostile_property_without_post(tmp_path):
    from tests.test_core_0982_loan_return_reconciliation import Session

    visited = []
    class Hostile(Session):
        @property
        def loan_inventory_snapshot(self):
            visited.append("getter")
            raise AssertionError("a snapshot property may issue an unwanted GET")

    loan = _loan()
    session = Hostile([loan])
    service = _library(tmp_path, session=session)
    try:
        result = service.return_digital(loan)
        assert result["status"] == "verification_unavailable"
        assert result["provider_inventory_verified"] is False
        assert session.posts == [(loan.id, "linked-b")]
        assert session.checks == []
        assert visited == []
        assert service.verify_return_digital(loan)["status"] == "verification_unavailable"
        assert len(session.posts) == 1
        assert visited == []
    finally:
        service.close()
