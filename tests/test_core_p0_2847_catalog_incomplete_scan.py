"""#2847: an incomplete local scan must never prove file disappearance.

All directories and SQLite rows are synthetic; no provider or real library.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _fixture(tmp_path, *, make_root=True):
    root = tmp_path / "library"
    if make_root:
        root.mkdir()
    store = Storage(tmp_path / "storage.sqlite")
    placement_id = store.placement_put({
        "artifact_id": None, "identity_id": "synthetic-identity",
        "provider_id": "local", "source_item_id": "synthetic-edition",
        "path": str(root / "Book.epub"), "state": "present",
        "metadata": {},
    })
    store.catalog_entry_put({
        "artifact_id": None, "placement_id": placement_id,
        "title": "Synthetic book", "identity_id": "synthetic-identity",
        "provider_id": "local", "item_id": "synthetic-edition",
        "local_state": "local", "metadata": {"path": str(root / "Book.epub")},
    })
    return store, root, placement_id


def _state(store, placement_id):
    return store.placement_get(placement_id)["state"]


def test_unmounted_or_deleted_root_does_not_appear_as_empty_library(tmp_path):
    store, root, placement_id = _fixture(tmp_path, make_root=False)
    assert not root.exists()
    with pytest.raises(FileNotFoundError, match="catalog_scan_root_unavailable"):
        LocalCatalog(store, root).scan()
    assert not root.exists()
    assert _state(store, placement_id) == "present"
    assert all(e["local_state"] == "local" for e in store.catalog_entries())


def test_unreadable_child_refuses_negative_transition(tmp_path, monkeypatch):
    store, root, placement_id = _fixture(tmp_path)

    def incomplete_walk(*_args, **_kwargs):
        yield root, [], []
        raise OSError("synthetic scandir failure")

    monkeypatch.setattr("cifereca.catalog.os.walk", incomplete_walk)
    with pytest.raises(OSError, match="synthetic scandir failure"):
        LocalCatalog(store, root).scan()
    assert _state(store, placement_id) == "present"
    assert all(e["local_state"] == "local" for e in store.catalog_entries())


def test_verified_empty_accessible_root_may_mark_missing(tmp_path):
    store, root, placement_id = _fixture(tmp_path)
    result = LocalCatalog(store, root).scan()
    assert result.missing_marked == 1
    assert _state(store, placement_id) == "missing"
    assert all(e["local_state"] == "missing" for e in store.catalog_entries())


def test_brand_new_empty_library_can_still_be_initialized(tmp_path):
    root = tmp_path / "library"
    store = Storage(tmp_path / "storage.sqlite")
    result = LocalCatalog(store, root).scan()
    assert root.is_dir()
    assert result.missing_marked == 0
