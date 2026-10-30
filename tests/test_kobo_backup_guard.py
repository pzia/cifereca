from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, SearchResult
from cifereca.providers import ProviderCapabilities, ProviderDescriptor, ProviderSession
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _write_epub(path: Path, marker: bytes = b"book") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", marker)


class KoboSession(ProviderSession):
    api_version = "2.3"
    supports_acquisition_display_profile = False
    supports_acquisition_acsm = False

    def __init__(self, *args, manifest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._manifest = list(manifest or [])

    def connect(self):
        self.connected = True
        self._accounts = [
            Account(
                display_name="Kobo",
                unique_id="account",
                provider_id="kobo",
                credential_id=self.credential_id,
                identity_id="alice",
            )
        ]
        return self.accounts

    def close(self):
        self.connected = False

    def backup_manifest(self):
        return list(self._manifest)

    def cover_url(self, _raw):
        return None

    def normalize_item(self, raw, default_collection="library"):
        return SearchResult(
            id=f"kobo:{self.credential_id}:{raw['product_id']}",
            record_id=str(raw["product_id"]),
            docbase="KOBO",
            medium="digital",
            title=str(raw.get("title") or raw["product_id"]),
            author="Author",
            type="Livre numérique Kobo",
            format=str(raw.get("format") or "EPUB"),
            publisher=None,
            published_at=None,
            description=None,
            thumbnail_url=None,
            friendly_url=None,
            scenario="KOBO",
            source_provider="kobo",
            source_credential=self.credential_id,
            content_type="ebook",
            source_collection=default_collection,
            owned=True,
            downloadable=True,
            revision_id=str(raw.get("revision_id") or "") or None,
            metadata={},
        )


def _manifest_row():
    return {
        "product_id": "book-1",
        "revision_id": "rev-1",
        "title": "Book One",
        "authors": ["Author"],
        "format": "EPUB",
        "content_type": "ebook",
        "owned": True,
        "downloadable": True,
    }


def _ready_state():
    return {
        "available": True,
        "compatible": True,
        "operational": True,
        "download_available": True,
        "acsm_available": False,
        "kobo_delegated_available": False,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": False,
        "kobo_formats": ["epub", "pdf"],
        "contract_version": 8,
        "strict_json_stdout": True,
        "software_version": "test",
        "error": None,
    }


def _service(tmp_path: Path):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set(
        "identities",
        [
            {"id": "alice", "label": "Alice", "directory_name": "alice"},
            {"id": "bob", "label": "Bob", "directory_name": "bob"},
        ],
    )
    config.set("providers.accounts", [])

    service = LibraryService(
        config,
        Storage(tmp_path / "db.sqlite3"),
        Diagnostics(level="detailed"),
    )
    service.storage.provider_connection_put({
        "identity_id": "alice",
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
    session = KoboSession(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
        manifest=[_manifest_row()],
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.kobo_adept_state = lambda **kwargs: _ready_state()
    service.runtime_diagnostic = lambda _plugin_id: {}
    return service


def _add_local_catalog(service: LibraryService, path: Path, *, identity_id: str, item_id: str) -> None:
    service.storage.catalog_entry_put({
        "catalog_entry_id": f"local:{identity_id}:{item_id}",
        "title": item_id,
        "identity_id": identity_id,
        "provider_id": "local",
        "item_id": item_id,
        "local_state": "local",
        "media_format": "epub",
        "metadata": {"path": str(path)},
    })


def test_unlinked_local_file_does_not_block_unrelated_kobo_acquisition(tmp_path: Path, monkeypatch):
    """#3196: another book is not evidence that this Kobo item already exists."""
    service = _service(tmp_path)
    imported = tmp_path / "EBooks" / "alice" / "local" / "imported.epub"
    _write_epub(imported, b"keep")
    original = imported.read_bytes()
    _add_local_catalog(service, imported, identity_id="alice", item_id="imported")
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})

    def acquire(_item, target_dir):
        artifact = Path(target_dir) / "downloaded.epub"
        _write_epub(artifact, b"fresh")
        return SimpleNamespace(path=artifact, account_id="account", metadata={})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        summary = service.sync_kobo_backup()
        assert service.kobo_unlinked_local_count("alice") == 1
        assert summary["attempted"] == summary["downloaded"] == 1
        assert summary["attention_required"] == 0
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row["status"] == "complete"
        assert Path(row["local_path"]) != imported
        assert imported.read_bytes() == original
    finally:
        service.close()


def test_explicit_permission_allows_acquisition_without_overwriting_unlinked_file(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    imported = tmp_path / "EBooks" / "alice" / "local" / "imported.epub"
    _write_epub(imported, b"original")
    original = imported.read_bytes()
    _add_local_catalog(service, imported, identity_id="alice", item_id="imported")
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})

    def acquire(_item, target_dir):
        artifact = Path(target_dir) / "book-one.epub"
        _write_epub(artifact, b"downloaded")
        return SimpleNamespace(path=artifact, account_id="account", metadata={"acquisition_mode": "http"})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        summary = service.sync_kobo_backup()

        assert service.kobo_unlinked_local_count("alice") == 1
        assert summary["attempted"] == 1
        assert summary["downloaded"] == 1
        assert imported.read_bytes() == original
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row is not None
        assert Path(row["local_path"]).resolve() != imported.resolve()
    finally:
        service.close()


def test_local_file_owned_by_another_identity_does_not_block(tmp_path: Path):
    service = _service(tmp_path)
    other = tmp_path / "EBooks" / "bob" / "local" / "other.epub"
    _write_epub(other)
    _add_local_catalog(service, other, identity_id="bob", item_id="other")
    try:
        assert service.kobo_unlinked_local_count("alice") == 0
        assert service.kobo_unlinked_local_count("bob") == 1
    finally:
        service.close()


def test_missing_local_file_does_not_block(tmp_path: Path):
    service = _service(tmp_path)
    missing = tmp_path / "EBooks" / "alice" / "local" / "missing.epub"
    _add_local_catalog(service, missing, identity_id="alice", item_id="missing")
    try:
        assert not missing.exists()
        assert service.kobo_unlinked_local_count("alice") == 0
    finally:
        service.close()


def test_complete_linked_backup_is_unchanged(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    artifact = tmp_path / "EBooks" / "alice" / "kobo" / "book-one.epub"
    _write_epub(artifact, b"already-linked")
    service.storage.backup_put({
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "account_id": "account",
        "identity_id": "alice",
        "item_id": "book-1",
        "revision_id": "rev-1",
        "title": "Book One",
        "author": "Author",
        "format": "EPUB",
        "status": "complete",
        "local_path": str(artifact),
        "size": artifact.stat().st_size,
        "sha256": None,
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("linked complete backup must be reused")),
    )
    try:
        summary = service.sync_kobo_backup()

        assert summary["unchanged"] == 1
        assert summary["attempted"] == 0
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row is not None
        assert row["status"] == "complete"
        assert Path(row["local_path"]).resolve() == artifact.resolve()
    finally:
        service.close()


def test_catalog_scan_still_precedes_acquisition_without_global_unlinked_guard(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    calls: list[str] = []

    def scan(*args, **kwargs):
        calls.append("scan")
        return {"scanned": 0}

    def acquire(_item, target_dir):
        calls.append("acquire")
        artifact = Path(target_dir) / "fresh.epub"
        _write_epub(artifact)
        return SimpleNamespace(path=artifact, account_id="account", metadata={})

    monkeypatch.setattr(service, "catalog_scan", scan)
    monkeypatch.setattr(service, "kobo_unlinked_local_count",
                        lambda _identity: pytest.fail("obsolete global unlinked guard"))
    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        summary = service.sync_kobo_backup()
        assert calls == ["scan", "acquire"]
        assert summary["downloaded"] == 1
        assert summary["attention_required"] == 0
    finally:
        service.close()


def test_existing_unlinked_kobo_filename_blocks_even_with_override(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    target = service._kobo_backup_target_dir("alice")
    existing = target / "Author - Book One.epub"
    _write_epub(existing, b"keep-existing")
    initial = existing.read_bytes()
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not redownload candidate")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert summary["reconcile_skipped"] == 1
        assert row["status"] == "reconcile_required"
        assert Path(row["local_path"]) == existing
        assert existing.read_bytes() == initial
    finally:
        service.close()


def test_existing_two_candidate_paths_block_as_conflict(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    target = service._kobo_backup_target_dir("alice")
    _write_epub(target / "Author - Book One.epub")
    _write_epub(target / "Author - Book One [book1].epub")
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not redownload candidates")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert summary["reconcile_conflicts"] == 1
        assert row["status"] == "reconcile_conflict"
        assert row["local_path"] is None
    finally:
        service.close()


def test_changed_revision_preserves_linked_nonstandard_filename(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    artifact = tmp_path / "EBooks" / "alice" / "kobo" / "renamed-by-user.epub"
    _write_epub(artifact, b"existing-linked-edition")
    original = artifact.read_bytes()
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    service.storage.backup_put({
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "account_id": "account",
        "identity_id": "alice",
        "item_id": "book-1",
        "revision_id": "old-revision",
        "title": "Book One",
        "author": "Author",
        "format": "EPUB",
        "status": "complete",
        "local_path": str(artifact),
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("revision update must not redownload")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert row["status"] == "reconcile_required"
        assert Path(row["local_path"]) == artifact
        assert artifact.read_bytes() == original
    finally:
        service.close()


def test_invalid_existing_destination_is_not_overwritten(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    destination = service._kobo_backup_target_dir("alice") / "Author - Book One.epub"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"corrupted or partial artifact")
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not overwrite invalid artifact")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert row["status"] == "reconcile_required"
        assert destination.read_bytes() == b"corrupted or partial artifact"
    finally:
        service.close()


def test_symlinked_existing_destination_is_not_followed(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    destination = service._kobo_backup_target_dir("alice") / "Author - Book One.epub"
    destination.parent.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "external.epub"
    external.write_bytes(b"external must remain intact")
    destination.symlink_to(external)
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not follow destination symlink")),
    )
    try:
        summary = service.sync_kobo_backup()
        assert summary["attempted"] == 0
        assert external.read_bytes() == b"external must remain intact"
    finally:
        service.close()


def test_existing_pdf_blocks_epub_manifest_acquisition(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    existing = service._kobo_backup_target_dir("alice") / "Author - Book One.pdf"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"%PDF-1.7\\nexisting")
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not duplicate PDF as EPUB")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert row["status"] == "reconcile_required"
        assert Path(row["local_path"]) == existing
    finally:
        service.close()


def test_renamed_reconcile_artifact_survives_recovery_without_redownload(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    moved = tmp_path / "EBooks" / "alice" / "renamed" / "my-book.epub"
    _write_epub(moved)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1", "identity_id": "alice",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Book One", "author": "Author", "format": "EPUB",
        "status": "reconcile_required", "local_path": str(moved),
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("renamed artifact must not redownload")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert row["status"] == "reconcile_required"
        assert Path(row["local_path"]) == moved
        assert moved.is_file()
    finally:
        service.close()


def test_absent_conflict_candidate_does_not_implicitly_clear_quarantine(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1", "identity_id": "alice",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Book One", "author": "Author", "format": "EPUB",
        "status": "reconcile_conflict", "local_path": None,
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("quarantine must not be reset")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert row["status"] == "reconcile_conflict"
    finally:
        service.close()


def test_dangling_symlink_after_interruption_is_quarantined_without_stat(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    target = service._kobo_backup_target_dir("alice") / "Author - Book One.epub"
    target.symlink_to(tmp_path / "not-present.epub")
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1", "identity_id": "alice",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Book One", "author": "Author", "format": "EPUB",
        "status": "acquiring", "local_path": None,
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dangling symlink must not redownload")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert row["status"] == "reconcile_required"
        assert Path(row["local_path"]) == target
        assert row["sha256"] is None
        assert target.is_symlink()
    finally:
        service.close()


def test_complete_symlink_is_quarantined_before_artifact_inspection(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    external = tmp_path / "external.epub"
    _write_epub(external)
    linked = service._kobo_backup_target_dir("alice") / "renamed.epub"
    linked.symlink_to(external)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1", "identity_id": "alice",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Book One", "author": "Author", "format": "EPUB",
        "status": "complete", "local_path": str(linked),
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service, "_observed_backup_artifact",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not inspect symlink target")),
    )
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not redownload")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert row["status"] == "reconcile_required"
        assert linked.is_symlink()
    finally:
        service.close()


def test_explicit_catalog_kobo_id_blocks_renamed_unlinked_file(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    renamed = tmp_path / "EBooks" / "alice" / "imports" / "unrelated-filename.epub"
    _write_epub(renamed)
    service.storage.catalog_entry_put({
        "catalog_entry_id": "local:alice:kobo-book-1",
        "identity_id": "alice", "provider_id": "kobo", "item_id": "book-1",
        "title": "Completely Different Name", "local_state": "local",
        "media_format": "epub", "metadata": {"path": str(renamed)},
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("explicit Kobo product link must block")),
    )
    try:
        summary = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert summary["attempted"] == 0
        assert row["status"] == "reconcile_required"
        assert Path(row["local_path"]) == renamed
    finally:
        service.close()


def test_catalog_kobo_link_of_other_identity_does_not_block(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    unrelated = tmp_path / "EBooks" / "bob" / "imports" / "book.epub"
    _write_epub(unrelated)
    service.storage.catalog_entry_put({
        "catalog_entry_id": "local:bob:kobo-book-1",
        "identity_id": "bob", "provider_id": "kobo", "item_id": "book-1",
        "title": "Book One", "local_state": "local",
        "media_format": "epub", "metadata": {"path": str(unrelated)},
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})

    def acquire(_item, target_dir):
        destination = Path(target_dir) / "new-acquisition.epub"
        _write_epub(destination)
        return SimpleNamespace(path=destination, account_id="account", metadata={"acquisition_mode": "http"})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        summary = service.sync_kobo_backup()
        assert summary["attempted"] == 1
        assert summary["downloaded"] == 1
        assert unrelated.is_file()
    finally:
        service.close()


def test_title_match_without_kobo_product_id_does_not_claim_same_book(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    unrelated = tmp_path / "EBooks" / "alice" / "local" / "named-differently.epub"
    _write_epub(unrelated)
    service.storage.catalog_entry_put({
        "catalog_entry_id": "local:alice:title-only",
        "identity_id": "alice", "provider_id": "local", "item_id": "unknown",
        "title": "Book One", "local_state": "local",
        "media_format": "epub", "metadata": {"path": str(unrelated)},
    })
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})

    def acquire(_item, target_dir):
        destination = Path(target_dir) / "new-acquisition.epub"
        _write_epub(destination)
        return SimpleNamespace(path=destination, account_id="account", metadata={"acquisition_mode": "http"})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        summary = service.sync_kobo_backup()
        assert summary["attempted"] == 1
        assert summary["downloaded"] == 1
    finally:
        service.close()


def test_direct_kobo_acquisition_rejects_existing_destination(tmp_path: Path, monkeypatch):
    from cifereca.providers import ProviderConfigurationError

    service = _service(tmp_path)
    item = service._sessions[service._session_key("kobo", "vault-1")].normalize_item(_manifest_row())
    existing = service._kobo_backup_target_dir("alice") / "Author - Book One.epub"
    _write_epub(existing)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("direct path must not redownload")),
    )
    try:
        try:
            service.acquire_direct_item(item)
        except ProviderConfigurationError as exc:
            assert "rapprochement" in str(exc)
        else:
            raise AssertionError("direct acquisition should refuse existing Kobo file")
        assert existing.is_file()
    finally:
        service.close()


def test_direct_kobo_acquisition_uses_shared_lock(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    item = service._sessions[service._session_key("kobo", "vault-1")].normalize_item(_manifest_row())
    lock_events = []

    class ProbeLock:
        def __init__(self, path):
            lock_events.append(("created", Path(path)))

        def __enter__(self):
            lock_events.append(("entered", None))
            return self

        def __exit__(self, *_args):
            lock_events.append(("exited", None))

    def acquire(_item, target):
        assert lock_events[-1][0] == "entered"
        artifact = Path(target) / "new.epub"
        _write_epub(artifact)
        return SimpleNamespace(path=artifact, account_id="account", metadata={})

    monkeypatch.setattr("cifereca.services.InterProcessLock", ProbeLock)
    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        service.acquire_direct_item(item)
        assert [event for event, _ in lock_events] == ["created", "entered", "exited"]
    finally:
        service.close()


def test_direct_kobo_download_rejects_renamed_catalog_product_link(tmp_path: Path, monkeypatch):
    from cifereca.providers import ProviderConfigurationError

    service = _service(tmp_path)
    item = service._sessions[service._session_key("kobo", "vault-1")].normalize_item(_manifest_row())
    renamed = tmp_path / "EBooks" / "alice" / "renamed" / "old-title.epub"
    _write_epub(renamed)
    service.storage.catalog_entry_put({
        "catalog_entry_id": "kobo:alice:book-1",
        "provider_id": "kobo", "identity_id": "alice",
        "item_id": "book-1", "title": "Other title",
        "local_state": "local", "metadata": {"path": str(renamed)},
    })
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not download")),
    )
    try:
        try:
            service.acquire_direct_item(item)
        except ProviderConfigurationError as exc:
            assert "rapprochement" in str(exc)
        else:
            raise AssertionError("renamed Kobo-local catalog entry was ignored")
        assert renamed.is_file()
    finally:
        service.close()


@pytest.mark.parametrize("malformed_opt_in", ["false", "true", 1, 0, None])
def test_obsolete_unlinked_override_does_not_gate_unrelated_books(
    tmp_path: Path, monkeypatch, malformed_opt_in,
):
    """Legacy configuration values cannot restore a global download veto."""
    service = _service(tmp_path)
    imported = tmp_path / "EBooks" / "alice" / "local" / "already-here.epub"
    _write_epub(imported, b"original")
    original = imported.read_bytes()
    _add_local_catalog(service, imported, identity_id="alice", item_id="already-here")
    service.config.set("kobo_backup.allow_unlinked_acquisition", malformed_opt_in)
    monkeypatch.setattr(service, "catalog_scan", lambda *_a, **_kw: {"scanned": 1})

    def acquire(_item, target_dir):
        destination = Path(target_dir) / "new-received.epub"
        _write_epub(destination, b"fresh")
        return SimpleNamespace(path=destination, account_id="account", metadata={})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        summary = service.sync_kobo_backup()
        assert summary["attempted"] == summary["downloaded"] == 1
        assert service.storage.backup_get(
            "kobo", "vault-1", "book-1",
        )["status"] == "complete"
        assert imported.read_bytes() == original
    finally:
        service.close()

