from __future__ import annotations
from packaging.version import Version

import tempfile
import zipfile
from pathlib import Path

from cifereca import __version__
from cifereca.acquisition import Acquisition
from cifereca.config import Config
from cifereca.core import CiferecaCore, LocalLibrarySearch, MaterializationCoordinator
from cifereca.diagnostics import Diagnostics
from cifereca.identities import Identity
from cifereca.models import SearchResult
from cifereca.paths import APP_NAME, config_dir, data_dir, cache_dir, state_dir, log_file
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _epub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("OEBPS/content.xhtml", "<html/>")


def test_step_a_namespace_and_version_are_cifereca():
    assert APP_NAME == "cifereca"
    assert Version(__version__) >= Version("0.9.2")


def test_step_a_core_is_single_composition_root():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        with CiferecaCore.open(config_path=root / "config.json", database_path=root / "library.sqlite3") as core:
            assert isinstance(core.config, Config)
            assert isinstance(core.storage, Storage)
            assert isinstance(core.diagnostics, Diagnostics)
            assert isinstance(core.library, LibraryService)
            assert core.storage.schema_version() == 14


def test_step_a_local_search_is_network_free_and_identity_scoped():
    rows = [
        {"key": "a", "identity_id": "alice", "title": "Le Grand Livre", "author": "Alice", "item_id": "1", "provider_id": "gallica"},
        {"key": "b", "identity_id": "bob", "title": "Autre livre", "author": "Bob", "item_id": "2", "provider_id": "paris"},
    ]
    calls: list[tuple[str | None, bool]] = []

    def entries(*, identity_id=None, scan=False):
        calls.append((identity_id, scan))
        return [row for row in rows if row["identity_id"] == identity_id]

    search = LocalLibrarySearch(entries)
    assert [row["key"] for row in search.search("grand livre", identity_id="alice")] == ["a"]
    assert search.search("grand livre", identity_id="bob") == []
    assert search.search("grand", identity_id="alice", scope="physical") == []
    assert calls == [("alice", False), ("bob", False)]


def test_step_a_materialization_registers_acquired_artifact_without_device_logic():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = Config(root / "config.json")
        cfg.set("library.directory", str(root / "EBooks"))
        service = LibraryService(cfg, Storage(root / "db.sqlite3"), Diagnostics())
        try:
            path = root / "EBooks" / "alice" / "gallica" / "Book.epub"
            _epub(path)
            item = SearchResult(
                id="gallica:g:1", record_id="ark-1", docbase="GALLICA", medium="digital",
                title="Book", author="Auteur", type="ebook", format="EPUB", publisher=None,
                published_at=None, description=None, thumbnail_url=None, friendly_url=None,
                scenario="GALLICA", source_provider="gallica", source_credential="g",
                download_url="https://example.invalid/book.epub", content_type="ebook",
            )
            acquired = Acquisition(
                provider_id="gallica", credential_id="g", account_id=None, item_id="ark-1",
                loan_id=None, kind="direct_file", source_url=None, local_path=path,
                mime_type="application/epub+zip", drm="none", artifact_path=path,
            )
            coordinator = MaterializationCoordinator(
                acquire=lambda _item: acquired,
                index_path=service.catalog_index_path,
                resolve_identity=lambda *_args: Identity(identity_id="alice", label="Alice", directory_name="alice"),
            )
            result = coordinator.obtain(item)
            assert result.path == path
            assert result.artifact_id and result.artifact_id.startswith("sha256:")
            assert result.indexed and result.indexed["status"] == "indexed"
            assert service.storage.artifact_get(result.artifact_id) is not None
        finally:
            service.close()


def test_step_a_xdg_namespace_is_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert config_dir() == tmp_path / "config" / "cifereca"
    assert data_dir() == tmp_path / "data" / "cifereca"
    assert cache_dir() == tmp_path / "cache" / "cifereca"
    assert state_dir() == tmp_path / "state" / "cifereca"
    assert log_file().name == "cifereca.log"


def test_step_a_kobo_reader_ui_and_cli_are_not_exposed():
    root = Path(__file__).resolve().parents[1]
    ui = (root / "src" / "cifereca" / "gui" / "main_window.py").read_text()
    cli = (root / "src" / "cifereca" / "cli.py").read_text()
    nav_block = ui.split("NAV_ITEMS = [", 1)[1].split("]", 1)[0]
    assert '("devices",' not in nav_block
    assert 'sub.add_parser("kobo-devices")' not in cli


def test_step_a_entrypoints_depend_on_core_composition_not_concrete_services():
    root = Path(__file__).resolve().parents[1]
    for name in ("app.py", "cli.py"):
        text = (root / "src" / "cifereca" / name).read_text()
        assert "from .core import CiferecaCore" in text
        assert "from .services import LibraryService" not in text
        assert "from .storage import Storage" not in text
