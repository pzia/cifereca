"""P0 #1346: owned Kobo inventory needs a durable, enabled account binding.

No provider request may occur for orphaned, rebound or ambiguous sessions.
All fixtures are fictional; no ADEPT, acquisition or network is used.
"""
from __future__ import annotations

from dataclasses import replace
import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from tests.test_core_1337_kobo_personal_manifest_rights import _service, _row


def _setup(service, *, credential="vault-fictional", identity="alice", bind=True):
    calls = []
    account = Account(
        display_name="Kobo owner", unique_id="store-owner",
        provider_id="kobo", credential_id=credential, identity_id=identity,
    )
    service.accounts = [account]
    if bind:
        service.storage.provider_connection_put({
            "provider_id": "kobo", "credential_id": credential,
            "identity_id": identity, "enabled": True,
        })

    def library():
        calls.append("library")
        yield _row(credential=credential)

    def forbidden(*_a, **_kw):
        pytest.fail("mirroring Kobo rights cannot acquire an ebook")

    service._sessions[service._session_key("kobo", credential)] = NS(
        provider_id="kobo", credential_id=credential, connected=True,
        capabilities=NS(personal_library=True, backup=True),
        library=library, download_library_item=forbidden,
    )
    return account, calls


def test_kobo_bound_owner_can_mirror_rights_without_acquisition(tmp_path):
    service = _service(tmp_path)
    try:
        account, calls = _setup(service)
        result = service.mirror_personal_libraries()
        assert result == {"kobo:vault-fictional": 1}
        assert result.blocked_sources == result.failed_sources == 0
        assert calls == ["library"]
        rights = service.storage.entitlements(provider_id="kobo")
        assert len(rights) == 1
        assert rights[0]["identity_id"] == account.identity_id
        assert not list((tmp_path / "EBooks").rglob("*.epub"))
    finally:
        service.close()


@pytest.mark.parametrize("invalid_binding", [
    "missing_account", "missing_binding", "rebound_identity",
    "disabled_connection", "duplicate_account", "unconfigured_identity",
])
def test_kobo_unbound_or_ambiguous_owner_is_never_queried(
    tmp_path, invalid_binding, caplog,
):
    service = _service(tmp_path)
    try:
        account, calls = _setup(
            service, bind=invalid_binding != "missing_binding",
        )
        if invalid_binding == "missing_account":
            service.accounts.clear()
        elif invalid_binding == "rebound_identity":
            service.storage.provider_connection_put({
                "provider_id": "kobo", "credential_id": account.credential_id,
                "identity_id": "bob", "enabled": True,
            })
        elif invalid_binding == "disabled_connection":
            service.storage.provider_connection_put({
                "provider_id": "kobo", "credential_id": account.credential_id,
                "identity_id": "alice", "enabled": False,
            })
        elif invalid_binding == "duplicate_account":
            service.accounts.append(replace(account, unique_id="second-owner"))
        elif invalid_binding == "unconfigured_identity":
            service.accounts[0] = replace(account, identity_id="unknown")
            service.storage.provider_connection_put({
                "provider_id": "kobo", "credential_id": account.credential_id,
                "identity_id": "unknown", "enabled": True,
            })
        with caplog.at_level(logging.WARNING):
            result = service.mirror_personal_libraries()
        assert result == {}
        assert result.blocked_sources == 1
        assert result.failed_sources == 0
        assert calls == []
        assert service.storage.entitlements(provider_id="kobo") == []
        assert service.storage.catalog_entries() == []
        assert "vault-fictional" not in caplog.text
    finally:
        service.close()


def test_disabled_kobo_source_never_requests_inventory(tmp_path):
    service = _service(tmp_path)
    try:
        account, calls = _setup(service)
        service.storage.metadata_set(
            "sync.source.enabled." + service.sync_source_id(account), "off",
        )
        result = service.mirror_personal_libraries()
        assert result == {}
        assert result.failed_sources == 0
        assert calls == []
        assert service.storage.entitlements(provider_id="kobo") == []
    finally:
        service.close()


def test_bad_kobo_binding_does_not_block_other_valid_personal_source(tmp_path):
    service = _service(tmp_path)
    try:
        first, broken_calls = _setup(service, credential="kobo-bad")
        service.accounts[0] = replace(first, identity_id="bob")
        valid, valid_calls = _setup(
            service, credential="kobo-good", identity="bob",
        )
        service.accounts.append(replace(first, identity_id="bob"))
        # Only one source has a matching persisted account/identity; both
        # are connected. The nonmatching source must not be fetched.
        result = service.mirror_personal_libraries()
        assert result == {"kobo:kobo-good": 1}
        assert result.blocked_sources == 1
        assert broken_calls == []
        assert valid_calls == ["library"]
        assert len(service.storage.entitlements(provider_id="kobo")) == 1
        assert service.storage.entitlements(provider_id="kobo")[0]["identity_id"] == "bob"
    finally:
        service.close()
