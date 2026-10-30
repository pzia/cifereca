from __future__ import annotations
from tests.gui_source import gui_source_text

import hashlib
from pathlib import Path

from cifereca.catalog import LocalCatalog
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import SearchResult
from cifereca.plugin_api import PLUGIN_API_VERSION
from cifereca.services import LibraryService
from cifereca.storage import CURRENT_SCHEMA_VERSION, Storage


def _service(tmp_path: Path) -> LibraryService:
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "library"))
    cfg.set("identities", [{"id": "alice", "label": "Alice"}])
    cfg.set("providers.accounts", [])
    return LibraryService(cfg, Storage(tmp_path / "db.sqlite3"), Diagnostics())


def _result(record_id: str) -> SearchResult:
    return SearchResult(
        id=f"remote:{record_id}", record_id=record_id, docbase="TEST", medium="digital",
        title=f"Book {record_id}", author="Author", type="ebook", format="EPUB", publisher=None,
        published_at=None, description=None, thumbnail_url=None, friendly_url=None,
        scenario="TEST", source_provider="remote", source_credential="remote-credential",
    )


def test_core044_versions_and_performance_schema(tmp_path: Path):
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        assert PLUGIN_API_VERSION == "1.4.3"
        assert CURRENT_SCHEMA_VERSION == 14
        assert storage.schema_version() == 14
        with storage._lock, storage._connect() as db:
            indexes = {row[1] for row in db.execute("PRAGMA index_list(catalog_entries)").fetchall()}
        assert {"idx_catalog_identity_state", "idx_catalog_source", "idx_catalog_placement"} <= indexes
    finally:
        storage.close()


def test_storage_reuses_connection_per_thread(tmp_path: Path):
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        first = storage._connect()
        second = storage._connect()
        assert first is second
    finally:
        storage.close()


def test_remote_mirror_uses_subset_lookup_and_batch_upsert(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        identity_id = service.current_identity.identity_id
        rows = [_result("1"), _result("2")]
        forbidden_calls = 0

        def forbidden_catalog_scan(*_args, **_kwargs):
            nonlocal forbidden_calls
            forbidden_calls += 1
            raise AssertionError("full catalog scan is forbidden on remote mirror hot path")

        monkeypatch.setattr(service.storage, "catalog_entries", forbidden_catalog_scan)
        assert service._mirror_remote_results(rows, identity_id=identity_id) == 2
        assert forbidden_calls == 0
        from cifereca.core.search_orchestration import _mirrored_catalogue_key
        keys = [_mirrored_catalogue_key(identity_id, row)[0] for row in rows]
        mirrored = service.storage.catalog_entries_by_ids(keys)
        assert set(mirrored) == set(keys)
        assert {row["item_id"] for row in mirrored.values()} == {"1", "2"}
    finally:
        service.close()


def test_targeted_catalog_index_does_not_scan_whole_catalog(tmp_path: Path, monkeypatch):
    storage = Storage(tmp_path / "db.sqlite3")
    root = tmp_path / "library"; root.mkdir()
    path = root / "sample.pdf"; path.write_bytes(b"%PDF-1.4\n% minimal fixture\n")
    catalog = LocalCatalog(storage, root)
    try:
        monkeypatch.setattr(storage, "catalog_entries", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected full catalog scan")))
        result = catalog.index_path(path, title="Sample")
        assert result["status"] == "indexed"
        entry_id = "catalog:" + hashlib.sha256(("placement:" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:32]).encode()).hexdigest()[:32]
        # The exact stable id is an implementation detail; the new point lookup
        # is exercised by index_path itself. Verify the persisted row through the
        # placement rather than reconstructing private IDs.
        placement = storage.placement_for_path(path.resolve())
        assert placement and placement["artifact_id"] == result["artifact_id"]
    finally:
        storage.close()


def test_gui_list_has_progressive_append_fast_path():
    source = gui_source_text(Path("src/cifereca/gui/main_window.py"))
    assert 'existing_keys == new_keys[:existing_count]' in source
    assert 'append-fast' in source


def test_new_progressive_search_supersedes_previous_without_waiting_for_provider(tmp_path, monkeypatch):
    import threading
    import time
    from types import SimpleNamespace

    class SlowSession:
        provider_id = "slow"
        credential_id = "slow-credential"
        connected = True
        capabilities = SimpleNamespace(search=True)
        descriptor = SimpleNamespace(display_name="Slow")
        client = None
        last_search_warnings = []
        def search(self, *_args, **_kwargs):
            time.sleep(0.35)
            return [_result("slow")]

    class FastSession(SlowSession):
        provider_id = "fast"
        credential_id = "fast-credential"
        descriptor = SimpleNamespace(display_name="Fast")
        def search(self, *_args, **_kwargs):
            return [_result("fast")]

    service = _service(tmp_path)
    monkeypatch.setattr(service, "_mirror_remote_results", lambda *_a, **_k: 0)
    first = {}
    try:
        service._sessions = {"slow": SlowSession()}
        thread = threading.Thread(target=lambda: first.setdefault("summary", service.search_progressive("one", force=True)))
        started = time.perf_counter(); thread.start(); time.sleep(0.05)
        service._sessions = {"fast": FastSession()}
        second = service.search_progressive("two", force=True)
        thread.join(timeout=0.20)
        assert not thread.is_alive(), "superseded search waited for obsolete provider"
        assert first["summary"].cancelled is True
        assert second.cancelled is False and second.result_count == 1
        assert time.perf_counter() - started < 0.30
    finally:
        service.close()
