"""P0 #2797: a partial Kobo ABI cannot infer rights from 0.7 manifest.

Canonical live races are covered by test_core_p0_4858_d1_canonical_preview.
The pre-ABI fence here denies any old manifest GET, ownership or B presence.
"""
from __future__ import annotations

from dataclasses import replace
import pytest
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_fault_isolation import _service

MANIFEST = [{"product_id": "synthetic-book", "revision_id": "r1", "format": "EPUB"}]

@pytest.mark.parametrize("change", [
    "disconnect", "replace_session", "replace_same_owner",
    "mutate_owner", "core_epoch",
])
def test_partial_abi_refuses_manifest_before_any_rebind_window(tmp_path, monkeypatch, change):
    service, session = _service(tmp_path, MANIFEST)
    calls = []
    monkeypatch.setattr(session, "backup_manifest",
                        lambda: calls.append("manifest") or MANIFEST)
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert calls == []
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()

def test_partial_abi_does_not_project_candidate_path_as_provider_right(tmp_path, monkeypatch):
    service, session = _service(tmp_path, MANIFEST)
    calls = []
    monkeypatch.setattr(service, "kobo_unlinked_local_count",
                        lambda *_a: calls.append("local evidence") or 0)
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert calls == []
    finally:
        service.close()

def test_disconnected_partial_abi_offline_requires_verified_account(tmp_path):
    service, session = _service(tmp_path, MANIFEST)
    session.connected = False
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_scope_unavailable"):
            service.kobo_backup_preview("vault-1")
    finally:
        service.close()

def test_partial_abi_cannot_advertise_potentially_missing(tmp_path):
    service, session = _service(tmp_path, MANIFEST)
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()

def test_ambiguous_borrowers_cannot_grant_partial_abi_refresh(tmp_path, monkeypatch):
    service, session = _service(tmp_path, MANIFEST)
    session._accounts.append(replace(session.accounts[0], unique_id="other"))
    monkeypatch.setattr(session, "backup_manifest",
                        lambda: pytest.fail("no 0.7 provider GET"))
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
    finally:
        service.close()
