"""#2847: a scan cannot mark a placement created after its inventory as missing.

All files and SQLite rows here are synthetic; concurrent writes are simulated
at the exact boundary before the negative SQLite transition.
"""
from __future__ import annotations

from pathlib import Path

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _placement(store, path, *, state="present", title="Example"):
    placement_id = store.placement_put({
        "artifact_id": None,
        "identity_id": "account-fixture",
        "provider_id": "fixture-provider",
        "source_item_id": title,
        "path": str(path),
        "state": state,
        "metadata": {},
    })
    store.catalog_entry_put({
        "artifact_id": None,
        "placement_id": placement_id,
        "title": title,
        "identity_id": "account-fixture",
        "provider_id": "fixture-provider",
        "item_id": title,
        "local_state": "missing" if state == "missing" else "local",
        "metadata": {"path": str(path)},
    })
    return placement_id


def _local_state(store, placement_id):
    return next(e["local_state"] for e in store.catalog_entries()
                if e["placement_id"] == placement_id)


def test_concurrent_new_placement_not_invalidated_by_older_scan(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    store = Storage(tmp_path / "storage.sqlite")
    old_id = _placement(store, root / "old-missing.pdf", title="old")
    new_path = root / "fresh.pdf"
    original = store.placements_mark_missing
    created = []

    def race_before_negative_transition(scan_root, seen_paths, **kwargs):
        # The file is published after rglob and after the final reappearance
        # guard. It did not exist when this inventory observed the directory.
        new_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        created.append(_placement(store, new_path, title="fresh"))
        return original(scan_root, seen_paths, **kwargs)

    monkeypatch.setattr(store, "placements_mark_missing", race_before_negative_transition)
    result = LocalCatalog(store, root).scan()

    assert result.missing_marked == 1
    assert store.placement_get(old_id)["state"] == "missing"
    assert store.placement_get(created[0])["state"] == "present"
    assert _local_state(store, created[0]) == "local"


def test_concurrent_reactivation_of_old_missing_row_is_preserved(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    store = Storage(tmp_path / "storage.sqlite")
    target_path = root / "restored.pdf"
    archived_id = _placement(store, target_path, state="missing", title="restored")
    original = store.placements_mark_missing

    def race_before_negative_transition(scan_root, seen_paths, **kwargs):
        target_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        assert _placement(store, target_path, title="restored") == archived_id
        return original(scan_root, seen_paths, **kwargs)

    monkeypatch.setattr(store, "placements_mark_missing", race_before_negative_transition)
    result = LocalCatalog(store, root).scan()

    assert result.missing_marked == 0
    assert store.placement_get(archived_id)["state"] == "present"
    assert _local_state(store, archived_id) == "local"


def test_direct_storage_call_keeps_its_legacy_behavior(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    store = Storage(tmp_path / "storage.sqlite")
    old_id = _placement(store, root / "missing.pdf")
    assert store.placements_mark_missing(root, set()) == 1
    assert store.placement_get(old_id)["state"] == "missing"
