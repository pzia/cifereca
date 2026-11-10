"""P0 #5054: explicit Numilog inventory sync does not project ownership.

Synthetic source rows only: no network, GET, file placement or entitlement.
"""
import pytest

from cifereca.core import sync_coordination
from cifereca.plugin_api import Cancelled
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_1346_numilog_metadata_only import _install
from tests.test_sync_state_contract import _service


def test_explicit_sync_observes_catalogue_without_persisting_pseudo_rights(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        (account,), calls = _install(service, monkeypatch, tmp_path)
        observed = []

        def canonical_stage(_service, source, **_kwargs):
            assert source is account
            observed.append("canonical")
            return {
                "status": "success", "observed": 0,
                "source_references_changed": 0,
                "present_attested": 0, "pending": 0,
            }

        monkeypatch.setattr(
            "cifereca.core.canonical_sync_stage.observe_canonical_rights_in_sync",
            canonical_stage,
        )
        result = service.synchronize_account_source(account)
        assert result["provider"] == "numilog"
        assert result["library"] == 1
        assert "owned_rights" not in result
        assert "rights_mirror_deferred" not in result
        assert result["canonical_rights"]["status"] == "success"
        assert observed == ["canonical"]
        assert calls == [
            ("manifest", "private-default"),
            ("mirror", "default", 1),
        ]
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()


def test_cancelled_inventory_read_aborts_before_any_catalogue_or_rights(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        (account,), calls = _install(service, monkeypatch, tmp_path)
        session = service._sessions[
            service._session_key(account.provider_id, account.credential_id)
        ]

        def interrupted():
            calls.append(("manifest", "private-default"))
            raise Cancelled("synthetic_user_cancelled")

        session.library = interrupted
        with pytest.raises(Cancelled, match="synthetic_user_cancelled"):
            service.synchronize_account_source(account)
        assert calls == [("manifest", "private-default")]
        assert service.storage.catalog_entries() == []
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()


def test_rebound_during_inventory_read_cannot_publish_remote_catalog(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        (account,), calls = _install(service, monkeypatch, tmp_path)
        session = service._sessions[
            service._session_key(account.provider_id, account.credential_id)
        ]

        def changed_binding():
            calls.append(("manifest", "private-default"))
            service.accounts = []
            return []

        session.library = changed_binding
        with pytest.raises(
            ProviderConfigurationError, match="private_inventory_source_changed",
        ):
            service.synchronize_account_source(account)
        assert calls == [("manifest", "private-default")]
        assert service.storage.catalog_entries() == []
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()
