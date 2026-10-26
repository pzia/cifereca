"""#2847: an observed local file must not become 'missing' due to scan timing.

Synthetic local PDFs and SQLite only. No acquisition, deletion or provider I/O.
"""
from __future__ import annotations

import os

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _case(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    file = root / "book.pdf"
    storage = Storage(tmp_path / "state.sqlite")
    placement_id = storage.placement_put({
        "path": str(file), "identity_id": "synthetic-profile",
        "provider_id": "local", "source_item_id": "synthetic-edition",
        "artifact_id": None, "state": "present", "metadata": {},
    })
    storage.catalog_entry_put({
        "placement_id": placement_id, "artifact_id": None,
        "title": "Synthetic book", "identity_id": "synthetic-profile",
        "provider_id": "local", "item_id": "synthetic-edition",
        "local_state": "local", "metadata": {"path": str(file)},
    })
    return storage, root, file, placement_id


def _late_change(monkeypatch, file, write):
    original_walk = os.walk

    def during_verification(*args, **kwargs):
        yield from original_walk(*args, **kwargs)
        write(file)

    monkeypatch.setattr("cifereca.catalog.os.walk", during_verification)


def _state(storage, placement_id):
    return storage.placement_get(placement_id)["state"]


def test_file_reappearing_after_rglob_cannot_be_marked_missing(tmp_path, monkeypatch):
    storage, root, file, placement_id = _case(tmp_path)
    assert not file.exists()
    _late_change(monkeypatch, file, lambda path: path.write_bytes(b"%PDF-1.7\nreappeared"))
    with pytest.raises(OSError, match="catalog_scan_changed_during_inventory"):
        LocalCatalog(storage, root).scan()
    assert file.read_bytes() == b"%PDF-1.7\nreappeared"
    assert _state(storage, placement_id) == "present"
    assert all(row["local_state"] == "local" for row in storage.catalog_entries())


def test_symlink_created_after_inventory_does_not_count_as_local_presence(tmp_path, monkeypatch):
    storage, root, file, placement_id = _case(tmp_path)
    outside = tmp_path / "external.pdf"
    outside.write_bytes(b"%PDF-1.7\noutside")
    _late_change(monkeypatch, file, lambda path: path.symlink_to(outside))
    result = LocalCatalog(storage, root).scan()
    assert result.missing_marked == 1
    assert _state(storage, placement_id) == "missing"
    assert outside.read_bytes() == b"%PDF-1.7\noutside"
    assert file.is_symlink()


def test_actual_file_absence_still_marks_missing(tmp_path):
    storage, root, file, placement_id = _case(tmp_path)
    result = LocalCatalog(storage, root).scan()
    assert result.missing_marked == 1
    assert _state(storage, placement_id) == "missing"
    assert all(row["local_state"] == "missing" for row in storage.catalog_entries())


def test_file_present_before_snapshot_stays_present(tmp_path):
    storage, root, file, placement_id = _case(tmp_path)
    file.write_bytes(b"%PDF-1.7\npresent")
    result = LocalCatalog(storage, root).scan()
    assert result.cataloged == 1 and result.missing_marked == 0
    assert _state(storage, placement_id) == "present"
