"""#3252: Kobo materialization inventory cannot outlive its Core owner.

Only fake Vault pages and a fake Core AccountRef broker, no HTTP, downloads,
rights mutation, credentials or Core persistence.
"""
from types import SimpleNamespace as NS

import pytest

import cifereca_kobo as provider
from cifereca.plugin_api import ProviderError
from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


def rig():
    vault = FakeVault([
        {"product_id": "owned-1", "account_id": "alice",
         "owned": True, "downloadable": True},
    ])
    session = session_with_vault(vault)
    session._accounts[0].identity_id = "identity-A"
    broker = NS(owner="identity-A")
    broker.accounts = lambda: [
        NS(provider_id="kobo", account_id="alice",
           credential_id=session.credential_id, scope_version=2,
           identity_id=broker.owner),
    ]
    session.identity_broker = broker
    return session, vault, broker


@pytest.mark.parametrize("race", [
    "owner_B", "broker_ABA", "owner_duplicated", "credential_v2_changed",
])
def test_kobo_backup_projection_rebind_never_returns_stale_manifest(
    monkeypatch, race,
):
    session, vault, broker = rig()
    original = provider.classify_personal_manifest
    def classify(rows, *, account_id, **kwargs):
        result = original(rows, account_id=account_id, **kwargs)
        if account_id == "alice":
            if race == "owner_B":
                broker.owner = "identity-B"
            elif race == "broker_ABA":
                newer = NS(owner="identity-A")
                newer.accounts = broker.accounts
                session.identity_broker = newer
            elif race == "owner_duplicated":
                original_accounts = broker.accounts
                broker.accounts = lambda: original_accounts() * 2
            else:
                broker.accounts = lambda: [
                    NS(provider_id="kobo", account_id="alice",
                       credential_id="other-credential", scope_version=2,
                       identity_id="identity-A"),
                ]
        return result

    monkeypatch.setattr(provider, "classify_personal_manifest", classify)
    try:
        with pytest.raises(ProviderError, match="propriétaire modifié"):
            session.backup_manifest()
        assert vault.manifest_calls == 1
        assert session.backup_manifest_rejection_count == 0
        assert session.backup_manifest_policy["manifest_authoritative"] is False
    finally:
        session.close()


def test_kobo_backup_projection_preserves_stable_credential_scope():
    session, vault, broker = rig()
    try:
        rows = session.backup_manifest()
        assert [x["product_id"] for x in rows] == ["owned-1"]
        assert vault.manifest_calls == 1
        assert broker.accounts()[0].credential_id == session.credential_id
        assert session.backup_manifest_policy["acquisition_authorized"] is False
    finally:
        session.close()
