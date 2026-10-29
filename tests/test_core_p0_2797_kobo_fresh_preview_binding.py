"""P0 #2797: refreshed Kobo preview cannot publish stale account presence.

All provider responses and rights are synthetic; no real Kobo requests,
acquisition, filenames, IDs or HTTP traces leave these tests.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_fault_isolation import _service


MANIFEST = [
    {"product_id": "synthetic-book", "revision_id": "r1", "format": "EPUB"},
]


@pytest.mark.parametrize("change", [
    "disconnect", "replace_session", "replace_same_owner",
    "mutate_owner", "core_epoch",
])
def test_refreshed_manifest_rebind_during_http_cannot_publish_presence(
    tmp_path, monkeypatch, change,
):
    service, session = _service(tmp_path, MANIFEST)
    original = session.backup_manifest
    invoked = []
    def raced():
        manifest = original()
        invoked.append("manifest")
        if change == "disconnect":
            session.connected = False
        elif change == "replace_session":
            service._sessions[service._session_key("kobo", "vault-1")] = object()
        elif change == "replace_same_owner":
            session._accounts = [replace(session.accounts[0])]
        elif change == "mutate_owner":
            session._accounts[0].unique_id = "different-owner"
        else:
            service._source_context_epoch += 1
        return manifest

    monkeypatch.setattr(session, "backup_manifest", raced)
    try:
        with pytest.raises(ProviderConfigurationError, match="Source Kobo modifiée"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert invoked == ["manifest"]
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()


def test_kobo_rebind_during_local_projection_rejects_fresh_positives(
    tmp_path, monkeypatch,
):
    service, session = _service(tmp_path, MANIFEST)
    checks = []
    def revoke_while_projecting(identity_id):
        checks.append(identity_id)
        # The HTTP answer is complete, but source credentials change before
        # file and personal-declaration projection starts.
        session.connected = False
        return 0

    monkeypatch.setattr(service, "kobo_unlinked_local_count", revoke_while_projecting)
    try:
        with pytest.raises(ProviderConfigurationError, match="Source Kobo modifiée"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert checks == ["default"]
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()


def test_disconnected_offline_kobo_checkpoint_preview_remains_available(tmp_path):
    service, session = _service(tmp_path, MANIFEST)
    session.connected = False
    try:
        result = service.kobo_backup_preview("vault-1")
        assert result["preview_only"] is True
        assert result["manifest_refreshed"] is False
        assert result["acquisition_authorized"] is False
    finally:
        service.close()


def test_refreshed_live_kobo_preview_preserves_read_only_partial_classification(tmp_path):
    service, session = _service(tmp_path, MANIFEST)
    try:
        result = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert result["preview_only"] is True
        assert result["manifest_refreshed"] is True
        assert result["known_item_count"] == 1
        assert result["counts"]["potentially_missing"] == 1
        assert result["acquisition_authorized"] is False
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()


def test_ambiguous_kobo_borrowers_remain_read_only_but_cannot_be_replaced_mid_read(
    tmp_path, monkeypatch,
):
    service, session = _service(tmp_path, MANIFEST)
    session._accounts.append(replace(session.accounts[0], unique_id="other"))
    try:
        stable = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert stable["preview_only"] is True
        assert stable["acquisition_authorized"] is False
        original = session.backup_manifest
        def changed_during_http():
            rows = original()
            session._accounts[1] = replace(session.accounts[1])
            return rows
        monkeypatch.setattr(session, "backup_manifest", changed_during_http)
        with pytest.raises(ProviderConfigurationError, match="Source Kobo modifiée"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
    finally:
        service.close()
