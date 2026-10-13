from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import zipfile

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


def test_unlinked_local_file_blocks_kobo_batch(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    imported = tmp_path / "EBooks" / "alice" / "local" / "imported.epub"
    _write_epub(imported, b"keep")
    _add_local_catalog(service, imported, identity_id="alice", item_id="imported")
    monkeypatch.setattr(service, "catalog_scan", lambda *args, **kwargs: {"scanned": 1})
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("guard must block acquisition")),
    )
    try:
        summary = service.sync_kobo_backup()

        assert service.kobo_unlinked_local_count("alice") == 1
        assert summary["attempted"] == 0
        assert summary["attention_required"] == 1
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row is not None
        assert row["status"] == "attention_required"
        assert row["attention_code"] == "unlinked_local_books"
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


def test_catalog_scan_runs_before_unlinked_guard(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    calls: list[str] = []

    def scan(*args, **kwargs):
        calls.append("scan")
        return {"scanned": 0}

    def guard(identity_id):
        assert identity_id == "alice"
        calls.append("guard")
        return 1

    monkeypatch.setattr(service, "catalog_scan", scan)
    monkeypatch.setattr(service, "kobo_unlinked_local_count", guard)
    monkeypatch.setattr(
        service,
        "_execute_kobo_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("guard must block acquisition")),
    )
    try:
        summary = service.sync_kobo_backup()

        assert calls[:2] == ["scan", "guard"]
        assert summary["attempted"] == 0
        assert summary["attention_required"] == 1
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
