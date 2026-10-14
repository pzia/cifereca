"""#1346: a personal backup manifest requires authenticated account authority.

No HTTP provider, local writes, download or DRM: FakeVault proves no upstream
manifest call occurs before a valid provider/credential/account binding.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import Account
from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


def _account(session, *, provider_id="kobo", credential_id=None, unique_id="alice"):
    return Account(
        unique_id=unique_id, display_name="Synthetic",
        provider_id=provider_id,
        credential_id=session.credential_id if credential_id is None else credential_id,
    )


@pytest.mark.parametrize("bad_binding", (
    "disconnected", "no_account", "two_accounts", "wrong_provider",
    "wrong_credential", "empty_account",
))
def test_backup_manifest_does_not_fetch_or_return_rows_without_verified_account(bad_binding):
    vault = FakeVault([{"product_id": "PRIVATE_BOOK", "owned": True,
                        "downloadable": True, "account_id": "alice"}])
    session = session_with_vault(vault)
    try:
        # A previous valid snapshot may have been cached for diagnostics;
        # invalidating authority must reset it before even considering Vault.
        assert len(session.backup_manifest()) == 1
        assert session.backup_manifest_policy["status"] == "classified"
        assert vault.manifest_calls == 1
        if bad_binding == "disconnected":
            session.connected = False
        elif bad_binding == "no_account":
            session._accounts = []
        elif bad_binding == "two_accounts":
            session._accounts.append(_account(session, unique_id="bob"))
        elif bad_binding == "wrong_provider":
            session._accounts = [_account(session, provider_id="other-provider")]
        elif bad_binding == "wrong_credential":
            session._accounts = [_account(session, credential_id="PRIVATE_FOREIGN_CREDENTIAL")]
        elif bad_binding == "empty_account":
            session._accounts = [_account(session, unique_id="")]

        assert session.backup_manifest() == []
        assert vault.manifest_calls == 1
        assert vault.acquisition_calls == 0
        assert session.backup_manifest_rejection_count == 0
        policy = session.backup_manifest_policy
        assert policy["status"] == "invalid_scope"
        assert policy["manifest_authoritative"] is False
        assert policy["acquisition_authorized"] is False
        assert policy["classified_items"] == 0
        diagnostic = json.dumps(session.diagnostic_state()["manifest_materialization_policy"])
        for secret in ("PRIVATE_BOOK", "PRIVATE_FOREIGN_CREDENTIAL", "alice"):
            assert secret not in diagnostic

        session.connected = True
        session._accounts = [_account(session)]
        assert [row["product_id"] for row in session.backup_manifest()] == ["PRIVATE_BOOK"]
        assert vault.manifest_calls == 2
        assert vault.acquisition_calls == 0
    finally:
        session.close()


def test_backup_manifest_unbound_account_never_sends_request_even_first_time():
    vault = FakeVault([{"product_id": "PRIVATE_BOOK", "owned": True}])
    session = session_with_vault(vault, connected=False)
    try:
        assert session.backup_manifest() == []
        assert vault.manifest_calls == 0
        assert session.backup_manifest_policy["status"] == "invalid_scope"
        assert session.backup_manifest_policy["acquisition_authorized"] is False
    finally:
        session.close()
