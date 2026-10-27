"""P0 #1120: malformed Paris loan rows must never prove PNB/Adobe absence.

All checks use offline provider stubs, real Core and SQLite receipts.
No live Paris requests, ADEPT mutation, file deletion, or acquisition.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from tests.test_core_0982_loan_return_reconciliation import (
    _library, _loan, Session,
)
from tests.test_core_0984_adept_first_paris_reconcile import setup as adept_setup


class HostileRow:
    @property
    def id(self):
        raise AssertionError("Untrusted row id must not be read")

    @property
    def provider(self):
        raise AssertionError("Untrusted row provider must not be read")

    def __str__(self):
        raise AssertionError("Untrusted row must never be stringified")


@pytest.mark.parametrize("row", [
    {"id": "loan-a", "provider": "DILICOM"},
    NS(id="loan-a", provider="DILICOM"),
    HostileRow(),
    replace(_loan(), id=""),
    replace(_loan(), provider=""),
    replace(_loan(), id=123),
    replace(_loan(), provider=object()),
])
def test_pnb_does_not_attest_return_with_malformed_inventory_row(tmp_path, row):
    session = Session([row])
    svc = _library(tmp_path, session=session)
    loan = _loan()
    try:
        outcome = svc.return_digital(loan)
        assert outcome["status"] == "verification_unavailable"
        assert outcome["verification"] == "unavailable"
        assert outcome["provider_inventory_verified"] is False
        assert session.posts == [(loan.id, "linked-b")]
        assert session.checks == ["linked-b"]
        assert svc.verify_return_digital(loan)["status"] == "verification_unavailable"
        assert session.posts == [(loan.id, "linked-b")]
    finally:
        svc.close()


@pytest.mark.parametrize("row", [
    {"id": "loan-a", "provider": "DILICOM"},
    NS(id="loan-a", provider="DILICOM"),
    HostileRow(),
    replace(_loan(), id=""),
    replace(_loan(), provider=123),
])
def test_adept_first_return_cannot_confirm_absence_from_bad_paris_row(tmp_path, row):
    svc, loan, paris = adept_setup(tmp_path)
    try:
        paris.inventory = [row]
        result = svc.verify_paris_return_inventory(loan)
        assert result == {
            "status": "verification_unavailable",
            "verification": "unavailable",
        }
        assert paris.posts == 0
        assert paris.accounts_read == ["linked-123"]
    finally:
        svc.close()


def test_genuine_paris_loan_list_and_typed_empty_inventory_have_distinct_meaning(tmp_path):
    session = Session([_loan()])
    svc = _library(tmp_path, session=session)
    try:
        active = svc.return_digital(_loan())
        assert active["status"] == "still_active"
        assert active["provider_inventory_verified"] is False
        session.inventory = []
        attest_paris_dilicom_complete(session, credential_id="paris-a")
        verified = svc.verify_return_digital(_loan())
        assert verified["status"] == "provider_return_confirmed"
        assert verified["provider_inventory_verified"] is True
        assert len(session.posts) == 1
    finally:
        svc.close()
