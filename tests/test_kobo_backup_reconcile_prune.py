from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.kobo.vault import KoboVaultError
from cifereca.models import Account
from cifereca.providers import ProviderCapabilities, ProviderDescriptor, ProviderSession
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _write_epub(path: Path, marker: bytes = b"book") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", marker)


class BackupSession(ProviderSession):
    api_version = "2.3"
    supports_acquisition_display_profile = False
    supports_acquisition_acsm = False

    def __init__(self, *args, manifest=None, manifest_error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._manifest = list(manifest or [])
        self._manifest_error = manifest_error

    def connect(self):
        self.connected = True
        self._accounts = [
            Account(
                display_name="Kobo",
                unique_id="account",
                provider_id="kobo",
                credential_id=self.credential_id,
                identity_id="default",
            )
        ]
        return self.accounts

    def close(self):
        self.connected = False

    def backup_manifest(self):
        if self._manifest_error is not None:
            raise self._manifest_error
        return list(self._manifest)

    def cover_url(self, _raw):
        return None


def _service(tmp_path: Path, *, manifest=None, manifest_error=None) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set(
        "identities",
        [{
            "id": "default",
            "label": "Hugues",
            "directory_name": "default",
            "preferences": {"reading_languages": ["fr"]},
        }],
    )
    config.set("providers.accounts", [])

    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))
    service.storage.provider_connection_put({
        "identity_id": "default",
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "label": "Kobo",
    })

    descriptor = ProviderDescriptor(
        id="kobo",
        display_name="Kobo",
        description="test",
        state="ready",
        capabilities=ProviderCapabilities(search=True, backup=True, personal_library=True),
    )
    session = BackupSession(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
        manifest=manifest,
        manifest_error=manifest_error,
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.kobo_unlinked_local_count = lambda identity_id: 0
    service.kobo_adept_state = lambda **kwargs: {
        "available": True,
        "compatible": True,
        "operational": True,
        "acsm_available": False,
        "kobo_delegated_available": False,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": False,
        "kobo_formats": ["epub", "pdf"],
        "contract_version": 8,
        "software_version": "test",
        "error": None,
    }
    return service


def _manifest_row(item_id="book-1", title="Book One", revision_id="rev-1"):
    return {
        "product_id": item_id,
        "revision_id": revision_id,
        "title": title,
        "authors": ["Author"],
        "format": "EPUB",
        "content_type": "ebook",
        "owned": True,
        "downloadable": True,
    }


def test_interrupted_row_absent_from_authoritative_manifest_is_recovered_then_pruned(tmp_path: Path):
    service = _service(tmp_path, manifest=[])
    try:
        service.storage.backup_put({
            "provider_id": "kobo",
            "credential_id": "vault-1",
            "identity_id": "default",
            "item_id": "orphan",
            "revision_id": "r0",
            "title": "Interrupted",
            "author": "Author",
            "format": "EPUB",
            "status": "acquiring",
            "local_path": None,
            "error": None,
        })

        summary = service.sync_kobo_backup()

        assert summary["recovered_interrupted"] == 1
        assert summary["legacy_pruned"] == 1
        assert service.storage.backup_get("kobo", "vault-1", "orphan") is None
    finally:
        service.close()


def test_single_local_candidate_is_quarantined_as_reconcile_required(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row()])
    try:
        target = service._kobo_backup_target_dir("default") / "Author - Book One.epub"
        _write_epub(target, b"already-downloaded")
        service.storage.backup_put({
            "provider_id": "kobo",
            "credential_id": "vault-1",
            "identity_id": "default",
            "item_id": "book-1",
            "revision_id": "rev-1",
            "title": "Book One",
            "author": "Author",
            "format": "EPUB",
            "status": "error",
            "local_path": None,
            "error": "Kobo-Adept : réponse JSON invalide",
        })
        monkeypatch.setattr(
            service,
            "_execute_kobo_acquisition",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reconcile item must not redownload")),
        )

        summary = service.sync_kobo_backup()
        record = service.storage.backup_get("kobo", "vault-1", "book-1")

        assert summary["downloaded"] == 0
        assert summary["recovered_unparsed"] == 1
        assert summary["reconcile_pending"] == 1
        assert summary["reconcile_skipped"] == 1
        assert record is not None
        assert record["status"] == "reconcile_required"
        assert record["local_path"] == str(target)
        assert record["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        assert target.is_file()
    finally:
        service.close()


def test_multiple_local_candidates_become_conflict_without_deletion(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row()])
    try:
        target_dir = service._kobo_backup_target_dir("default")
        base = target_dir / "Author - Book One.epub"
        suffixed = target_dir / "Author - Book One [book1].epub"
        _write_epub(base, b"base")
        _write_epub(suffixed, b"duplicate")
        service.storage.backup_put({
            "provider_id": "kobo",
            "credential_id": "vault-1",
            "identity_id": "default",
            "item_id": "book-1",
            "revision_id": "rev-1",
            "title": "Book One",
            "author": "Author",
            "format": "EPUB",
            "status": "error",
            "local_path": None,
            "error": "Kobo-Adept : réponse JSON invalide",
        })
        monkeypatch.setattr(
            service,
            "_execute_kobo_acquisition",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("conflicted item must not redownload")),
        )

        summary = service.sync_kobo_backup()
        record = service.storage.backup_get("kobo", "vault-1", "book-1")

        assert summary["downloaded"] == 0
        assert summary["reconcile_conflicts"] == 1
        assert record is not None
        assert record["status"] == "reconcile_conflict"
        assert base.is_file()
        assert suffixed.is_file()
    finally:
        service.close()


def test_successful_manifest_prune_preserves_local_artifact_and_history(tmp_path: Path):
    service = _service(tmp_path, manifest=[])
    try:
        # Even a fully registered credential and borrower do not turn a
        # v0.7 manifest into a typed, negative provider-right attestation.
        current = next(c for c in service.storage.provider_connections()
                       if c["provider_id"] == "kobo" and c["credential_id"] == "vault-1")
        service.storage.provider_account_put({
            "connection_id": current["connection_id"],
            "identity_id": "default", "provider_id": "kobo",
            "credential_id": "vault-1", "account_id": "account",
        })
        local = service._kobo_backup_target_dir("default") / "Old Local.epub"
        _write_epub(local, b"old-local")
        service.catalog_index_path(
            local,
            title="Old Local",
            item_id="old-local",
            identity_id="default",
            provider_id="kobo",
        )

        service.storage.backup_put({
            "provider_id": "kobo",
            "credential_id": "vault-1",
            "identity_id": "default",
            "account_id": "account",
            "item_id": "old-local",
            "revision_id": "old-r1",
            "title": "Old Local",
            "author": "Author",
            "format": "EPUB",
            "status": "complete",
            "local_path": str(local),
            "size": local.stat().st_size,
            "sha256": hashlib.sha256(local.read_bytes()).hexdigest(),
        })
        service.storage.backup_put({
            "provider_id": "kobo",
            "credential_id": "vault-1",
            "identity_id": "default",
            "account_id": "account",
            "item_id": "old-remote",
            "revision_id": "old-r2",
            "title": "Old Remote",
            "author": "Author",
            "format": "EPUB",
            "status": "complete",
        })

        before_rights = [
            row for row in service.storage.entitlements(provider_id="kobo")
            if row.get("item_id") in {"old-local", "old-remote"}
        ]
        assert len(before_rights) == 2

        summary = service.sync_kobo_backup()

        assert summary["errors"] == 0
        assert summary["legacy_entitlements_inactivated"] == 0
        assert summary["legacy_pruned"] == 2
        assert summary["legacy_local_preserved"] == 1
        assert service.storage.backup_get("kobo", "vault-1", "old-local") is None
        assert service.storage.backup_get("kobo", "vault-1", "old-remote") is None
        assert local.is_file()

        stale_entitlements = [
            row
            for row in service.storage.entitlements(provider_id="kobo")
            if row.get("item_id") in {"old-local", "old-remote"}
        ]
        assert stale_entitlements == before_rights
        assert {row["state"] for row in stale_entitlements} == {"active"}

        entries = [
            row
            for row in service.storage.catalog_entries(state="local")
            if row.get("item_id") == "old-local"
        ]
        assert entries
        assert Path(entries[0]["metadata"]["path"]).resolve() == local.resolve()
    finally:
        service.close()


def test_failed_manifest_run_never_prunes_stale_projection(tmp_path: Path):
    service = _service(
        tmp_path,
        manifest_error=KoboVaultError(
            "manifest indisponible",
            code="temporary",
            status_code=503,
        ),
    )
    try:
        service.storage.backup_put({
            "provider_id": "kobo",
            "credential_id": "vault-1",
            "identity_id": "default",
            "item_id": "stale",
            "revision_id": "r0",
            "title": "Stale",
            "format": "EPUB",
            "status": "complete",
        })

        summary = service.sync_kobo_backup()

        assert summary["errors"] == 1
        assert summary["legacy_pruned"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "stale") is not None
    finally:
        service.close()
