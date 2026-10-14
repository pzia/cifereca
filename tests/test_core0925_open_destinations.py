from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api import write_bundle_manifest
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("providers.accounts", [])
    config.set("identities", [
        {"id": "alice", "label": "Alice", "directory_name": "alice"},
        {"id": "bob", "label": "Bob", "directory_name": "bob"},
    ])
    return LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))


def _placement(service: LibraryService, path: Path, *, identity_id="alice", artifact_id="sha256:test", source_item_id="book"):
    return service.storage.placement_put({
        "artifact_id": artifact_id,
        "path": str(path),
        "identity_id": identity_id,
        "provider_id": "local",
        "source_item_id": source_item_id,
        "state": "present",
        "metadata": {},
    })


def test_local_book_path_rechecks_identity_and_exact_file(tmp_path: Path):
    service = _service(tmp_path)
    path = tmp_path / "EBooks" / "alice" / "local" / "Book.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"book")
    placement_id = _placement(service, path)
    try:
        assert service.local_book_path(placement_id, identity_id="alice") == path.resolve()
        try:
            service.local_book_path(placement_id, identity_id="bob")
            raise AssertionError("cross-identity open must be refused")
        except Exception:
            pass
    finally:
        service.close()


def test_bundle_open_uses_declared_playlist(tmp_path: Path):
    service = _service(tmp_path)
    bundle = tmp_path / "EBooks" / "alice" / "local" / "Audio"
    bundle.mkdir(parents=True)
    (bundle / "track.mp3").write_bytes(b"audio")
    (bundle / "play.m3u8").write_text("#EXTM3U\ntrack.mp3\n", encoding="utf-8")
    write_bundle_manifest(bundle, title="Audio", playlist="play.m3u8")
    placement_id = _placement(service, bundle, artifact_id="sha256:bundle")
    try:
        assert service.local_book_path(placement_id, identity_id="alice") == (bundle / "play.m3u8").resolve()
    finally:
        service.close()


def test_remote_detail_requires_unique_acquisition_attested_placement(tmp_path: Path):
    service = _service(tmp_path)
    first = tmp_path / "EBooks" / "alice" / "paris" / "one.epub"
    second = tmp_path / "EBooks" / "alice" / "paris" / "two.epub"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    service.storage.acquisition_put({
        "acquisition_id": "acq-1",
        "identity_id": "alice",
        "provider_id": "paris",
        "credential_id": "card",
        "item_id": "book",
        "status": "complete",
        "artifact_id": "sha256:a",
        "metadata": {"path": str(first)},
    })
    _placement(service, first, artifact_id="sha256:a")
    try:
        row = service.local_placement_for_source(
            provider_id="paris", credential_id="card", item_id="book", identity_id="alice"
        )
        assert row is not None

        _placement(service, second, artifact_id="sha256:a", source_item_id="book-2")
        assert service.local_placement_for_source(
            provider_id="paris", credential_id="card", item_id="book", identity_id="alice"
        ) is None
    finally:
        service.close()


def test_materialize_to_identity_reuses_existing_local_artifact_without_acquisition(tmp_path: Path):
    service = _service(tmp_path)
    path = tmp_path / "EBooks" / "alice" / "local" / "Book.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"same bytes")
    placement_id = _placement(service, path)
    service.storage.catalog_entry_put({
        "catalog_entry_id": "local:alice:book",
        "artifact_id": "sha256:test",
        "placement_id": placement_id,
        "title": "Book",
        "author": "Author",
        "identity_id": "alice",
        "provider_id": "local",
        "item_id": "book",
        "local_state": "local",
        "media_format": "epub",
        "metadata": {"path": str(path)},
    })
    service.execute_acquisition_request = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("identity transfer must not download or fulfill ACSM")
    )
    try:
        result = service.materialize_to_identity(placement_id, "bob")
        assert Path(result["path"]).is_file()
        assert Path(result["path"]).read_bytes() == b"same bytes"
    finally:
        service.close()


def test_open_action_rechecks_path_at_click_and_reports_launch_error():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    tree = ast.parse(source.with_name("local_book_action_controller.py").read_text())
    method = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "add_local_book_actions")
    text = ast.unparse(method)

    assert "local_book_path(placement_id, identity_id=identity_id).as_uri()" in text
    assert "Gio.AppInfo.launch_default_for_uri" in text
    assert "show_error" in text
    assert "materialize_to_identity" in text
    assert "execute_acquisition_request" not in text
