"""#1346: Kobo inventory projection cannot leak rows across authenticated accounts.

Offline FakeVault tests only: no Kobo service, acquisition, file or Core writes.
"""
from __future__ import annotations

import json

from cifereca.plugin_api import Account
from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


def test_library_filters_foreign_account_rows_and_colliding_local_variant():
    vault = FakeVault([
        {"product_id": "PRIVATE_COLLISION", "account_id": "alice",
         "title": "PRIVATE_LOCAL", "owned": True},
        {"product_id": "PRIVATE_FOREIGN", "account_id": "bob",
         "title": "PRIVATE_FOREIGN_TITLE", "owned": True},
        {"product_id": "PRIVATE_COLLISION", "account_id": "bob",
         "title": "PRIVATE_WRONG_REVISION", "owned": True},
        {"product_id": "visible-1", "account_id": "alice", "owned": True},
        {"product_id": "legacy-2", "owned": True},
    ])
    session = session_with_vault(vault)
    try:
        rows = session.library()
        assert [row.record_id for row in rows] == ["visible-1", "legacy-2"]
        assert all(row.owned is True for row in rows)
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
        assert session.last_library_rejections == [
            {"type": "invalid_scope", "reason": "ambiguous_account_binding"},
            {"type": "invalid_scope", "reason": "different_account"},
            {"type": "invalid_scope", "reason": "different_account"},
        ]
        # No private product, account or title in the additive diagnostic block.
        info = json.dumps(session.diagnostic_state()["library_rejections"])
        for secret in ("PRIVATE_", "bob", "alice", "visible-1", "Bearer"):
            assert secret not in info
    finally:
        session.close()


def test_disconnected_or_ambiguous_binding_makes_no_manifest_request():
    vault = FakeVault([{"product_id": "private-book", "owned": True}])
    session = session_with_vault(vault)
    try:
        session.connected = False
        assert session.library() == []
        assert session.last_library_rejections == [
            {"type": "invalid_scope", "reason": "unbound_account"},
        ]
        assert vault.manifest_calls == 0

        session.connected = True
        session._accounts.append(Account(
            unique_id="other", display_name="Private other",
            provider_id="kobo", credential_id=session.credential_id,
        ))
        assert session.library() == []
        assert vault.manifest_calls == 0

        session._accounts.pop()
        assert [row.record_id for row in session.library()] == ["private-book"]
        assert session.last_library_rejections == []
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
    finally:
        session.close()


def test_library_three_accounts_are_isolated_with_account_specific_manifest():
    vaults = [
        FakeVault([{"product_id": f"item-{i}", "account_id": f"account-{i}",
                    "owned": True}])
        for i in range(1, 4)
    ]
    sessions = [
        session_with_vault(vault, account_id=f"account-{i}")
        for i, vault in enumerate(vaults, start=1)
    ]
    try:
        assert [row.record_id for row in sessions[1].library()] == ["item-2"]
        assert [vault.manifest_calls for vault in vaults] == [0, 1, 0]
        vaults[1].manifest = [
            {"product_id": "PRIVATE_WRONG", "account_id": "account-3", "owned": True},
        ]
        assert sessions[1].library() == []
        assert [vault.manifest_calls for vault in vaults] == [0, 2, 0]
        assert sessions[1].last_library_rejections == [
            {"type": "invalid_scope", "reason": "different_account"},
        ]
        assert [row.record_id for row in sessions[0].library()] == ["item-1"]
        assert [row.record_id for row in sessions[2].library()] == ["item-3"]
        assert [vault.manifest_calls for vault in vaults] == [1, 2, 1]
        assert all(vault.acquisition_calls == 0 for vault in vaults)
    finally:
        for session in sessions:
            session.close()


def test_untrusted_product_identity_not_normalized_to_foreign_or_local_owned():
    vault = FakeVault([
        {"id": "PRIVATE_Q", "product_id": "PRIVATE_P", "account_id": "alice",
         "owned": True},
        {"product_id": "safe", "account_id": "alice", "owned": True},
    ])
    session = session_with_vault(vault)
    try:
        assert [row.record_id for row in session.library()] == ["safe"]
        assert session.last_library_rejections == [
            {"type": "ProviderError", "reason": "missing_product_id"},
        ]
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
    finally:
        session.close()


def test_wrong_provider_or_credential_binding_is_not_inventory_authority():
    vault = FakeVault([{"product_id": "private-book", "owned": True}])
    session = session_with_vault(vault)
    try:
        for provider, credential in (
            ("other-provider", session.credential_id),
            ("kobo", "PRIVATE_OTHER_CREDENTIAL"),
        ):
            session._accounts = [Account(
                unique_id="alice", display_name="Private",
                provider_id=provider, credential_id=credential,
            )]
            assert session.library() == []
            assert session.last_library_rejections == [
                {"type": "invalid_scope", "reason": "unbound_account"},
            ]
            assert vault.manifest_calls == 0
        session._accounts = [Account(
            unique_id="alice", display_name="Private",
            provider_id="kobo", credential_id=session.credential_id,
        )]
        assert [row.record_id for row in session.library()] == ["private-book"]
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
    finally:
        session.close()
