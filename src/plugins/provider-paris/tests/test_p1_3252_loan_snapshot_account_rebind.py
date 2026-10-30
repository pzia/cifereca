"""#3252/#3122: Paris snapshot pins the borrower across its legacy read.

No login, real portal requests, files, ACSM, acquisition or private content.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import Account, ProviderConfigurationError
from tests.test_p1_3252_loan_snapshot_connect_close_serialization import fixture


@pytest.mark.parametrize("change", [
    "replace_same_scope", "replace_different_identity", "drop_account",
    "duplicate_same_scope", "mutate_caller_identity", "mutate_active_identity",
])
def test_snapshot_refuses_borrower_rebind_even_with_same_account_values(change):
    session, account, client = fixture()
    original = session._read_loans_for_account
    invoked = []

    def changed_during_read(borrower):
        rows = original(borrower)
        invoked.append("read")
        if change in {"replace_same_scope", "replace_different_identity"}:
            new_account = replace(account)
            if change == "replace_different_identity":
                new_account.identity_id = "other-identity"
            session._accounts = [new_account]
        elif change == "drop_account":
            session._accounts = []
        elif change == "duplicate_same_scope":
            session._accounts.append(replace(account))
        elif change == "mutate_caller_identity":
            account.identity_id = "other-identity"
        else:
            session._accounts[0].identity_id = "other-identity"
        return rows

    session._read_loans_for_account = changed_during_read
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(account)
        assert invoked == ["read"]
        assert [call[1] for call in client.calls if call[0] == "loans"] == [
            "DILICOM", "SYRACUSE",
        ]
    finally:
        session.close()


def test_copy_of_current_borrower_remains_compatible_with_core_scope_lookup():
    session, account, client = fixture()
    try:
        caller = replace(account)
        assert caller is not account
        observed = session.loan_inventory_snapshot(caller)
        assert len(observed.loans) == 2
        assert observed.status == "success"
        assert observed.scope.account_id == ""
        assert all(coverage.completeness == "unknown" for coverage in observed.branches)
    finally:
        session.close()


def test_cloned_borrower_from_other_credential_is_denied_before_any_loan_get():
    session, account, client = fixture()
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(replace(account, credential_id="other-card"))
        assert all(call[0] != "loans" for call in client.calls)
    finally:
        session.close()
