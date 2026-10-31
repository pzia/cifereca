"""#3171: optional Numilog rights mirroring cannot veto canonical delivery.

Synthetic authenticated private rows and source Sync, no HTTP/ACSM/GET.
"""
import pytest

from cifereca.core import sync_coordination
from tests.test_core_p0_1346_numilog_metadata_only import _install
from tests.test_sync_state_contract import _service


def _broken_mirror(*_args, **_kwargs):
    raise RuntimeError("synthetic private SQLite rights projection unavailable")


def test_explicit_numilog_sync_continues_core_after_optional_mirror_failure(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        (account,), calls = _install(service, monkeypatch, tmp_path)
        monkeypatch.setattr(
            "cifereca.core.numilog_manifest_rights.mirror_numilog_manifest_rights",
            _broken_mirror,
        )
        observed = []
        def canonical_stage(_service, source, **_kwargs):
            assert source is account
            observed.append("canonical")
            return {
                "status": "success", "observed": 0,
                "source_references_changed": 0, "present_attested": 0,
                "pending": 0,
            }
        monkeypatch.setattr(
            "cifereca.core.canonical_sync_stage.observe_canonical_rights_in_sync",
            canonical_stage,
        )
        result = service.synchronize_account_source(account)
        assert result["provider"] == "numilog"
        assert result["library"] == 1
        assert result["owned_rights"] == 0
        assert result["rights_mirror_deferred"] is True
        assert result["status"] == "partial"
        assert result["canonical_rights"]["status"] == "success"
        assert observed == ["canonical"]
        assert calls == [
            ("manifest", "private-default"),
            ("mirror", "default", 1),
        ]
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()


def test_optional_mirror_cancellation_still_aborts_sync(
    tmp_path, monkeypatch,
):
    from cifereca.plugin_api import Cancelled

    service = _service(tmp_path)
    try:
        (account,), calls = _install(service, monkeypatch, tmp_path)
        def interrupted(*_args, **_kwargs):
            raise Cancelled("synthetic_user_cancelled")
        monkeypatch.setattr(
            "cifereca.core.numilog_manifest_rights.mirror_numilog_manifest_rights",
            interrupted,
        )
        with pytest.raises(Cancelled, match="synthetic_user_cancelled"):
            service.synchronize_account_source(account)
        assert calls == [("manifest", "private-default")]
        assert service.storage.placements() == []
    finally:
        service.close()


def test_rebound_during_failed_mirror_cannot_publish_remote_catalog(
    tmp_path, monkeypatch,
):
    from cifereca.providers import ProviderConfigurationError

    service = _service(tmp_path)
    try:
        (account,), calls = _install(service, monkeypatch, tmp_path)
        def changed_binding(*_args, **_kwargs):
            service.accounts = []
            raise ValueError("synthetic private mirror error after rebound")
        monkeypatch.setattr(
            "cifereca.core.numilog_manifest_rights.mirror_numilog_manifest_rights",
            changed_binding,
        )
        with pytest.raises(ProviderConfigurationError, match="private_inventory_source_changed"):
            service.synchronize_account_source(account)
        assert calls == [("manifest", "private-default")]
        assert service.storage.placements() == []
    finally:
        service.close()
