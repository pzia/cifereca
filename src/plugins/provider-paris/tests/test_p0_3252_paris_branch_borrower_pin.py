"""P0 #3252: rebind during DILICOM cannot send SYRACUSE GET to another borrower.

Synthetic clients and accounts only. No Paris service, HTTP, credentials,
ADEPT/ACSM, loan mutation, storage or reconciliation.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import ProviderConfigurationError
from tests.test_p1_3252_atomic_scoped_loan_inventory import fixture


@pytest.mark.parametrize(("field", "changed"), [
    ("unique_id", "reader-B"), ("identity_id", "identity-B"),
    ("credential_id", "another-card"), ("provider_id", "other-provider"),
])
@pytest.mark.parametrize("fail_first_get", [False, True])
def test_rebinding_during_dilicom_never_calls_syracuse_for_foreign_account(
    field, changed, fail_first_get,
):
    session, account, _other = fixture()
    calls = []

    def loans(branch, borrower_id):
        calls.append((branch, borrower_id))
        if branch == "DILICOM":
            setattr(account, field, changed)
            if fail_first_get:
                raise RuntimeError("SYNTHETIC_UPSTREAM_OUTAGE")
        return []

    session.client.loans = loans
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(account)
        # Even a successful DILICOM response does not allow a foreign
        # SYRACUSE GET; a failed response cannot turn this into partial.
        assert calls == [("DILICOM", "reader-A")]
    finally:
        session.close()


def test_account_rebound_during_second_branch_discards_entire_typed_read():
    session, account, _other = fixture()
    calls = []

    def loans(branch, borrower_id):
        calls.append((branch, borrower_id))
        if branch == "SYRACUSE":
            account.identity_id = "identity-B"
        return []

    session.client.loans = loans
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(account)
        assert calls == [("DILICOM", "reader-A"), ("SYRACUSE", "reader-A")]
    finally:
        session.close()


def test_transient_borrower_aba_never_redirects_a_later_branch_get():
    """Even an undetectable A->B->A still uses the pinned A for both GETs."""
    session, account, _other = fixture()
    calls = []

    def loans(branch, borrower_id):
        calls.append((branch, borrower_id))
        if branch == "DILICOM":
            account.unique_id = "reader-B"
            account.unique_id = "reader-A"
        return []

    session.client.loans = loans
    try:
        snapshot = session.loan_inventory_snapshot(account)
        assert snapshot.status == "success"
        assert snapshot.loans == ()
        assert snapshot.scope.account_id == "reader-A"
        assert calls == [("DILICOM", "reader-A"), ("SYRACUSE", "reader-A")]
        assert not snapshot.branch_claims_complete("dilicom", "digital")
    finally:
        session.close()


def test_legacy_reader_also_stops_before_second_private_get():
    session, account, _other = fixture()
    calls = []

    def loans(branch, borrower_id):
        calls.append((branch, borrower_id))
        account.unique_id = "reader-B"
        return []

    session.client.loans = loans
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(account)
        assert calls == [("DILICOM", "reader-A")]
    finally:
        session.close()
