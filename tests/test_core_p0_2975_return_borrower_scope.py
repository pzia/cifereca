"""P0 #2975: a foreign Paris borrower inventory never proves this loan absent.

Provider and ADEPT return paths share the same fail-closed source check.
All data are synthetic; no provider requests, EPUB writes or extra return POST.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

from dataclasses import replace
import json

import pytest

from cifereca.core.loan_return_verification import _paris_inventory_exact_borrower
from tests.test_core_0982_loan_return_reconciliation import _library, _loan, Session
from tests.test_core_0984_adept_first_paris_reconcile import setup as adept_setup


PRIVATE = "PRIVATE_OTHER_BORROWER_DO_NOT_EXPORT"


@pytest.mark.parametrize("field,foreign_value", [
    ("source_account_id", PRIVATE),
    ("source_account_id", None),
    ("source_account_id", ""),
    ("source_credential", PRIVATE),
    ("source_provider", "kobo"),
    ("identity_id", "other"),
    ("identity_id", 7),
])
def test_pnb_return_never_confirms_absence_from_foreign_scope(
    tmp_path, field, foreign_value,
):
    target = _loan()
    foreign = replace(
        _loan(loan_id="different-loan"), **{field: foreign_value},
    )
    session = Session([foreign])
    svc = _library(tmp_path, session=session)
    try:
        result = svc.return_digital(target)
        assert result["status"] == "verification_unavailable"
        assert result["verification"] == "unavailable"
        assert result["provider_inventory_verified"] is False
        assert result["retry"] == "verification_first"
        assert session.posts == [(target.id, "linked-b")]
        assert session.checks == ["linked-b"]
        assert PRIVATE not in json.dumps(result)
        # Reverification is *inventory only*, even after restart/retry.
        again = svc.verify_return_digital(target)
        assert again["status"] == "verification_unavailable"
        assert again["provider_inventory_verified"] is False
        assert session.posts == [(target.id, "linked-b")]
        assert session.checks == ["linked-b", "linked-b"]
    finally:
        svc.close()


@pytest.mark.parametrize("field,foreign_value", [
    ("source_account_id", PRIVATE),
    ("source_credential", "other"),
    ("source_provider", "other"),
    ("identity_id", "other"),
])
def test_adobe_first_paris_return_does_not_use_foreign_borrower_for_absence(
    tmp_path, field, foreign_value,
):
    svc, loan, paris = adept_setup(tmp_path)
    paris.inventory = [
        replace(loan, id="different-loan", **{field: foreign_value}),
    ]
    try:
        result = svc.verify_paris_return_inventory(loan)
        assert result == {
            "status": "verification_unavailable",
            "verification": "unavailable",
        }
        assert paris.accounts_read == ["linked-123"]
        assert paris.posts == 0
        assert PRIVATE not in json.dumps(result)
    finally:
        svc.close()


def test_mixed_exact_and_foreign_rows_cannot_be_used_as_exhaustive_inventory(
    tmp_path,
):
    target = _loan()
    other = replace(
        target, id="different-loan", source_account_id=PRIVATE,
    )
    session = Session([target, other])
    svc = _library(tmp_path, session=session)
    try:
        result = svc.return_digital(target)
        assert result["status"] == "verification_unavailable"
        assert result["provider_inventory_verified"] is False
        assert session.posts == [(target.id, "linked-b")]
        assert PRIVATE not in json.dumps(result)
    finally:
        svc.close()


def test_complete_exact_borrower_rows_allow_absence_only_for_selected_loan(
    tmp_path,
):
    target = _loan()
    # Other titles of the SAME borrower do not make this loan still active.
    # Typed pagination coverage must carry the account's exact identity.
    other = replace(_loan(loan_id="other-loan"), identity_id=target.identity_id)
    session = Session([other])
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    svc = _library(tmp_path, session=session)
    try:
        result = svc.return_digital(target)
        assert result["status"] == "provider_return_confirmed"
        assert result["verification"] == "provider_absent"
        assert result["provider_inventory_verified"] is True
        assert session.posts == [(target.id, "linked-b")]
        assert session.checks == ["linked-b"]
    finally:
        svc.close()


def test_paris_primary_account_sentinel_is_exact_borrower_not_missing(tmp_path):
    loan = _loan(uid="primary", loan_id="primary-loan")
    session = Session([replace(loan, id="other-primary-loan")])
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    svc = _library(tmp_path, session=session)
    try:
        result = svc.return_digital(loan)
        assert result["status"] == "provider_return_confirmed"
        assert result["provider_inventory_verified"] is True
        assert session.posts == [(loan.id, "")]
        assert session.checks == [""]
        primary = next(a for a in svc.accounts if a.is_primary and a.credential_id == "paris-a")
        assert _paris_inventory_exact_borrower(session.inventory, primary)
        assert not _paris_inventory_exact_borrower(
            session.inventory, next(a for a in svc.accounts if not a.is_primary),
        )
    finally:
        svc.close()


def test_malformed_inventory_row_types_fail_without_coercion(tmp_path):
    class Hostile:
        def __eq__(self, _other):
            raise AssertionError("untrusted equality invoked")
        def __str__(self):
            raise AssertionError("untrusted stringification invoked")

    row = replace(_loan(loan_id="OTHER"), source_account_id=Hostile())
    session = Session([row])
    svc = _library(tmp_path, session=session)
    try:
        result = svc.return_digital(_loan())
        assert result["status"] == "verification_unavailable"
        assert not result["provider_inventory_verified"]
        assert session.posts == [("loan-a", "linked-b")]
    finally:
        svc.close()
