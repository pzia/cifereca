"""Kobo P0: preflight shows potential acquisitions without mutating rights.

The default preview works strictly offline. Explicit refresh requests only
the provider's rights manifest, never acquisition descriptors or content.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_fault_isolation import _service, _write_epub


def _backup(service, item_id, *, status="pending", local_path=None, **extra):
    service.storage.backup_put({
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "identity_id": "default",
        "item_id": item_id,
        "revision_id": "r1",
        "title": item_id,
        "format": "EPUB",
        "status": status,
        "local_path": str(local_path) if local_path is not None else None,
        **extra,
    })


def _forbid_mutation_and_acquisition(service, session, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("preview must not mutate Core, download or invoke Adept")

    monkeypatch.setattr(service.storage, "backup_put", forbidden)
    monkeypatch.setattr(service.storage, "metadata_set", forbidden)
    monkeypatch.setattr(service, "_execute_kobo_acquisition", forbidden)
    monkeypatch.setattr(service, "kobo_adept_state", forbidden)
    monkeypatch.setattr(service, "catalog_scan", forbidden)
    monkeypatch.setattr(session, "resolve_acquisition", forbidden)


def test_offline_preview_is_readonly_and_requires_live_authority(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path, [])
    target_dir = service._kobo_backup_target_dir("default")
    linked = target_dir / "linked.epub"
    _write_epub(linked)
    _backup(service, "linked", status="complete", local_path=linked)
    _backup(service, "candidate")
    candidate = target_dir / (service.acquisition._safe_filename("candidate") + ".epub")
    _write_epub(candidate)
    _backup(
        service, "blocked", status="attention_required",
        attention_code="missing_delegation_scope",
    )
    _backup(service, "missing")
    before = {
        name: service.storage.backup_get("kobo", "vault-1", name)
        for name in ("linked", "candidate", "blocked", "missing")
    }
    monkeypatch.setattr(
        session, "backup_manifest",
        lambda: pytest.fail("offline preview must not contact provider"),
    )
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    try:
        result = service.kobo_backup_preview("vault-1")
        assert result["scope"] == "account"
        assert result["manifest_refreshed"] is False
        assert result["requires_manifest_validation"] is True
        assert result["acquisition_authorized"] is False
        assert result["manifest_items"] == 4
        assert result["counts"] == {
            "already_local_linked": 1, "already_local_verified": 0,
            "needs_reconciliation": 1, "attention_required": 0, "skipped_archived": 0,
            "blocked_scope": 1, "blocked_unlinked": 0, "not_downloadable": 0,
            "candidate_unverified": 1, "potentially_missing": 0,
            "invalid_manifest_items": 0,
        }
        assert result["blocking_items"] == 2
        assert len(result["examples"]) == 3
        public = json.dumps(result)
        for private in ("vault-1", "linked.epub", "candidate.epub", '"item_id"',
                        '"credential_id"', "https://"):
            assert private not in public
        for item in ("linked", "candidate", "blocked", "missing"):
            assert service.storage.backup_get("kobo", "vault-1", item) == before[item]
        assert linked.is_file() and candidate.is_file()
    finally:
        service.close()


def test_refresh_preview_reads_only_manifest_and_handles_malformed_items(tmp_path: Path, monkeypatch):
    manifest = [
        {"product_id": "saved", "title": "Saved", "revision_id": "r1", "format": "EPUB"},
        {"product_id": "new", "title": "New", "revision_id": "r2", "format": "EPUB"},
        {"product_id": "archived", "title": "Archived", "archived": True},
        {"product_id": "unavailable", "downloadable": False},
        {"title": "without product ID"},
        {"product_id": "new", "title": "Duplicate ID"},
        None,
    ]
    service, session = _service(tmp_path, manifest)
    saved = service._kobo_backup_target_dir("default") / "saved.epub"
    _write_epub(saved)
    _backup(service, "saved", status="complete", local_path=saved)
    calls = []
    def only_manifest():
        calls.append("manifest")
        return list(manifest)
    monkeypatch.setattr(session, "backup_manifest", only_manifest)
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    try:
        result = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert calls == ["manifest"]
        assert result["manifest_refreshed"] is True
        assert result["requires_manifest_validation"] is False
        assert result["manifest_valid"] is False
        assert result["acquisition_authorized"] is False
        assert result["manifest_items"] == 7
        assert result["known_item_count"] == 4
        counts = result["counts"]
        assert counts["already_local_linked"] == 1
        assert counts["potentially_missing"] == 1
        assert counts["attention_required"] == 0
        assert counts["skipped_archived"] == 1
        assert counts["not_downloadable"] == 1
        assert counts["invalid_manifest_items"] == 3
        assert "new" not in json.dumps(result)
        assert service.storage.backup_get("kobo", "vault-1", "new") is None
    finally:
        service.close()


def test_preview_never_creates_target_and_does_not_block_on_unrelated_import(tmp_path: Path):
    service, _session = _service(tmp_path, [])
    untouched = tmp_path / "never-created-by-preview"
    service.config.set("kobo_backup.directory", str(untouched))
    _backup(service, "purchase")
    service.kobo_unlinked_local_count = lambda _id: 3
    try:
        result = service.kobo_backup_preview("vault-1")
        assert result["unlinked_local_books"] == 3  # diagnostic retained
        assert result["counts"]["blocked_unlinked"] == 0
        assert result["counts"]["candidate_unverified"] == 1
        assert not untouched.exists()
        assert result["acquisition_authorized"] is False  # preview is not consent
    finally:
        service.close()


def test_verify_files_rejects_invalid_linked_epub_without_changing_record(tmp_path: Path):
    service, _session = _service(tmp_path, [])
    target = service._kobo_backup_target_dir("default") / "bad.epub"
    # Corrupt payload with no ZIP/PDF signature: the normal loose-EPUB
    # fixture is accepted by the non-strict inspector.
    target.write_bytes(b"this is not an EPUB file")
    _backup(
        service, "bad", status="complete", local_path=target,
        sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        size=target.stat().st_size,
    )
    before = service.storage.backup_get("kobo", "vault-1", "bad")
    try:
        result = service.kobo_backup_preview(
            "vault-1", verify_local_files=True,
        )
        assert result["verification"] == "artifact_hash"
        # Hash + size match the stored row, but content is not an EPUB.
        assert result["counts"]["needs_reconciliation"] == 1
        assert result["counts"]["already_local_verified"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "bad") == before
    finally:
        service.close()


def test_invalid_credential_and_disconnected_manifest_refresh_fail_closed(tmp_path: Path):
    service, session = _service(tmp_path, [])
    try:
        with pytest.raises(ProviderConfigurationError, match="non configuré"):
            service.kobo_backup_preview("unlisted")
        # The offline preview remains available when a provider disconnects.
        session.connected = False
        result = service.kobo_backup_preview("vault-1")
        assert result["manifest_refreshed"] is False
        with pytest.raises(ProviderConfigurationError, match="non connecté"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
    finally:
        service.close()


def test_remote_author_list_candidate_is_ambiguous_not_downloadable(tmp_path: Path, monkeypatch):
    service, session = _service(tmp_path, [{
        "product_id": "remote-1", "title": "The Novel",
        "authors": ["Jane Doe"], "revision_id": "r1",
        "format": "EPUB",
    }])
    target = service._kobo_backup_target_dir("default")
    candidate = target / (
        service.acquisition._safe_filename("Jane Doe - The Novel") + ".epub"
    )
    _write_epub(candidate)
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    try:
        plan = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert plan["counts"]["needs_reconciliation"] == 1
        assert plan["counts"]["potentially_missing"] == 0
        assert plan["acquisition_authorized"] is False
        assert service.storage.backup_get("kobo", "vault-1", "remote-1") is None
        assert candidate.is_file()
    finally:
        service.close()


def test_offline_preview_never_confuses_same_product_id_across_identities(tmp_path: Path):
    from dataclasses import replace

    service, _session = _service(tmp_path, [])
    other_identity = replace(
        service.identities[0],
        identity_id="another", label="Another", directory_name="another",
    )
    service.identities.append(other_identity)
    service.storage.identity_put(other_identity)
    service.storage.provider_connection_put({
        "identity_id": "another", "provider_id": "kobo",
        "credential_id": "vault-2", "label": "Other",
    })
    path = service._kobo_backup_target_dir("another") / "remote.epub"
    _write_epub(path)
    _backup(service, "shared-book", status="pending")
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-2",
        "identity_id": "another", "item_id": "shared-book",
        "revision_id": "r1", "title": "Shared", "format": "EPUB",
        "status": "complete", "local_path": str(path),
    })
    try:
        first = service.kobo_backup_preview("vault-1")
        second = service.kobo_backup_preview("vault-2")
        assert first["source_id"] != second["source_id"]
        assert first["counts"]["candidate_unverified"] == 1
        assert first["counts"]["already_local_linked"] == 0
        assert second["counts"]["already_local_linked"] == 1
        assert second["counts"]["candidate_unverified"] == 0
        assert path.is_file()
    finally:
        service.close()


@pytest.mark.parametrize("malformed_opt_in", ["false", "true", 1, 0, None])
def test_kobo_preview_obsolete_unlinked_override_never_restores_global_veto(
    tmp_path: Path, malformed_opt_in,
):
    service, session = _service(tmp_path, [
        {"product_id": "purchase", "revision_id": "r1", "format": "EPUB",
         "downloadable": True},
    ])
    _backup(service, "purchase")
    service.kobo_unlinked_local_count = lambda _id: 3
    service.config.set("kobo_backup.allow_unlinked_acquisition", malformed_opt_in)
    try:
        preview = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert preview["unlinked_local_books"] == 3
        assert preview["counts"]["blocked_unlinked"] == 0
        assert preview["counts"]["potentially_missing"] == 1
        assert preview["blocking_items"] == 0
        assert preview["acquisition_authorized"] is False
    finally:
        service.close()


@pytest.mark.parametrize("status", ["complete", "ok"])
def test_preview_quarantines_prior_complete_row_missing_any_link(
    tmp_path: Path, monkeypatch, status,
):
    service, session = _service(tmp_path, [])
    _backup(service, "already-acquired", status=status, local_path=None)
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    try:
        result = service.kobo_backup_preview("vault-1")
        assert result["counts"]["needs_reconciliation"] == 1
        assert result["counts"]["candidate_unverified"] == 0
        assert result["counts"]["potentially_missing"] == 0
        assert result["blocking_items"] == 1
        assert result["acquisition_authorized"] is False
    finally:
        service.close()
