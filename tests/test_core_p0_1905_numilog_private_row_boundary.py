"""P0 #1905: private Numilog batches cannot mirror another account's records."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.providers import ProviderConfigurationError
from cifereca.core import numilog_manifest_rights
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_sync_state_contract import _service


@pytest.mark.parametrize("mode", ["opening", "manual"])
@pytest.mark.parametrize("bad", [
    "foreign_credential", "public_catalog", "false_ownership",
    "wrong_provider", "wrong_variant", "not_a_result",
])
def test_private_inventory_rejects_mixed_batch_before_any_write(
    tmp_path, mode, bad,
):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        valid = _row(account.credential_id, 0)
        bogus = {
            "foreign_credential": lambda: _row("foreign-account", 1),
            "public_catalog": lambda: replace(
                valid, id=_row(account.credential_id, 1).id,
                record_id=_row(account.credential_id, 1).record_id,
                source_collection="catalog", scenario="NUMILOG_PUBLIC",
            ),
            "false_ownership": lambda: replace(valid, owned=False),
            "wrong_provider": lambda: replace(valid, source_provider="kobo"),
            "wrong_variant": lambda: replace(
                valid, id="numilog:invalid:lid1:abcd",
                record_id="numilog:invalid:lid1:abcd",
            ),
            "not_a_result": lambda: object(),
        }[bad]()
        session.library = lambda: (calls.append("library") or [valid, bogus])
        if mode == "opening":
            result = service.mirror_personal_libraries()
            assert result == {}
            assert result.blocked_sources == 1
        else:
            with pytest.raises(ProviderConfigurationError):
                service._synchronize_account_source_impl(account)
        assert calls == ["library"]
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.catalog_entries() == []
    finally:
        service.close()


@pytest.mark.parametrize("mode", ["opening", "manual"])
def test_numilog_source_revoked_during_rights_projection_cannot_publish_catalogue(
    tmp_path, monkeypatch, mode,
):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=1)
        original = numilog_manifest_rights.mirror_numilog_manifest_rights

        def revoke_after_projection(*args, **kwargs):
            count = original(*args, **kwargs)
            service.storage.provider_connection_put({
                "identity_id": "default", "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": False,
            })
            return count

        monkeypatch.setattr(
            numilog_manifest_rights, "mirror_numilog_manifest_rights",
            revoke_after_projection,
        )
        if mode == "opening":
            result = service.mirror_personal_libraries()
            assert result == {}
            assert result.blocked_sources == 1
        else:
            with pytest.raises(ProviderConfigurationError):
                service._synchronize_account_source_impl(account)
        assert calls == ["library"]
        assert service.storage.catalog_entries() == []
    finally:
        service.close()
