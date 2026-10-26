"""#2847: an interrupted scan cannot turn an unmounted library into missing books.

SQLite and filesystem fixtures are local and synthetic; no network or provider.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _existing_placement(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    store = Storage(tmp_path / "storage.sqlite")
    placement_id = store.placement_put({
        "artifact_id": None,
        "identity_id": "fixture-account",
        "provider_id": "fixture-provider",
        "source_item_id": "fixture-book",
        "path": str(root / "already-indexed.epub"),
        "state": "present",
        "metadata": {},
    })
    store.catalog_entry_put({
        "artifact_id": None,
        "placement_id": placement_id,
        "title": "Example",
        "identity_id": "fixture-account",
        "provider_id": "fixture-provider",
        "item_id": "fixture-book",
        "local_state": "local",
        "metadata": {"path": str(root / "already-indexed.epub")},
    })
    return root, store, placement_id


def _assert_not_marked_missing(store, placement_id):
    assert store.placement_get(placement_id)["state"] == "present"
    assert all(entry["local_state"] == "local"
               for entry in store.catalog_entries())


def test_root_disappears_during_walk_without_scandir_error(tmp_path, monkeypatch):
    root, store, placement_id = _existing_placement(tmp_path)

    def disappearing_walk(*_args, **_kwargs):
        root.rmdir()
        if False:
            yield

    monkeypatch.setattr("cifereca.catalog.os.walk", disappearing_walk)
    with pytest.raises(OSError, match="catalog_scan_root_changed"):
        LocalCatalog(store, root).scan()
    assert not root.exists()
    _assert_not_marked_missing(store, placement_id)


def test_root_replaced_by_empty_directory_during_walk(tmp_path, monkeypatch):
    root, store, placement_id = _existing_placement(tmp_path)
    moved = tmp_path / "original-library"

    def replaced_walk(*_args, **_kwargs):
        root.rename(moved)
        root.mkdir()
        yield str(root), [], []

    monkeypatch.setattr("cifereca.catalog.os.walk", replaced_walk)
    with pytest.raises(OSError, match="catalog_scan_root_changed"):
        LocalCatalog(store, root).scan()
    assert root.is_dir() and moved.is_dir()
    _assert_not_marked_missing(store, placement_id)


def test_stable_empty_library_still_marks_really_missing_book(tmp_path):
    root, store, placement_id = _existing_placement(tmp_path)
    result = LocalCatalog(store, root).scan()
    assert result.missing_marked == 1
    assert store.placement_get(placement_id)["state"] == "missing"


def test_clean_new_library_remains_initializable(tmp_path):
    root = tmp_path / "new-library"
    store = Storage(tmp_path / "storage.sqlite")
    result = LocalCatalog(store, root).scan()
    assert root.is_dir()
    assert result.missing_marked == 0
