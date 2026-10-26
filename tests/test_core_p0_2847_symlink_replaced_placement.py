"""#2847: a symlink substitution never preserves a stale present placement.

All files, owners and records are synthetic; no external provider or mutation.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _case(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    file = root / "owned.pdf"
    file.write_bytes(b"%PDF-1.7\nowned\n")
    storage = Storage(tmp_path / "database.sqlite")
    placement_id = storage.placement_put({
        "artifact_id": "sha256:synthetic-artifact",
        "path": str(file),
        "identity_id": "synthetic-identity",
        "provider_id": "local", "source_item_id": "synthetic-edition",
        "state": "present", "metadata": {},
    })
    storage.catalog_entry_put({
        "artifact_id": "sha256:synthetic-artifact",
        "placement_id": placement_id, "title": "Synthetic",
        "identity_id": "synthetic-identity",
        "provider_id": "local", "item_id": "synthetic-edition",
        "local_state": "local", "metadata": {"path": str(file)},
    })
    return storage, root, file, placement_id


def _state(storage, placement_id):
    return storage.placement_get(placement_id)["state"]


def test_replaced_leaf_symlink_does_not_retain_present(tmp_path):
    storage, root, file, placement_id = _case(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7\nunrelated\n")
    file.unlink()
    file.symlink_to(outside)

    assert storage.placements_mark_missing(root, set()) == 1

    assert _state(storage, placement_id) == "missing"
    assert storage.catalog_entries()[0]["local_state"] == "missing"
    assert file.is_symlink()
    assert outside.read_bytes() == b"%PDF-1.7\nunrelated\n"


def test_replaced_parent_symlink_does_not_retain_present(tmp_path):
    storage, root, file, placement_id = _case(tmp_path)
    original = root / "original"
    original.mkdir()
    new_name = original / "inside.pdf"
    # Create a second independent placement under a directory that will move.
    new_name.write_bytes(b"%PDF-1.7\nsecond\n")
    child_id = storage.placement_put({
        "path": str(new_name), "artifact_id": None,
        "identity_id": "synthetic-identity", "provider_id": "local",
        "source_item_id": "second-edition", "state": "present", "metadata": {},
    })
    original.rename(root / "saved-original")
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "inside.pdf").write_bytes(b"%PDF-1.7\nexternal\n")
    original.symlink_to(outside, target_is_directory=True)

    assert storage.placements_mark_missing(root, {str(file)}) == 1

    assert _state(storage, placement_id) == "present"
    assert _state(storage, child_id) == "missing"
    assert (outside / "inside.pdf").read_bytes() == b"%PDF-1.7\nexternal\n"


def test_present_file_observed_by_complete_scan_remains_present(tmp_path):
    storage, root, file, placement_id = _case(tmp_path)

    assert storage.placements_mark_missing(root, {str(file.resolve())}) == 0
    assert _state(storage, placement_id) == "present"
    assert storage.catalog_entries()[0]["local_state"] == "local"


def test_placement_outside_scanned_root_is_not_touched(tmp_path):
    storage, root, _, _ = _case(tmp_path)
    foreign = tmp_path / "external" / "other.pdf"
    foreign.parent.mkdir()
    foreign.write_bytes(b"%PDF-1.7\nexternal\n")
    foreign_id = storage.placement_put({
        "path": str(foreign), "artifact_id": None,
        "identity_id": "foreign-identity", "provider_id": "local",
        "source_item_id": "foreign-edition", "state": "present", "metadata": {},
    })

    storage.placements_mark_missing(root, set())

    assert _state(storage, foreign_id) == "present"


@pytest.mark.parametrize("raw_kind", ["relative", "traversal"])
def test_untrusted_noncanonical_legacy_path_is_not_marked(tmp_path, raw_kind):
    storage, root, _, _ = _case(tmp_path)
    raw = ("relative/untrusted.pdf" if raw_kind == "relative"
           else str(root / ".." / "outside.pdf"))
    placement_id = "synthetic-legacy"
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO placements(placement_id,path,identity_id,provider_id,state,"
            "metadata_json,created_at,updated_at) VALUES(?,?,?,?,'present','{}',?,?)",
            (placement_id, raw, "synthetic-identity", "local", 1, 1),
        )

    storage.placements_mark_missing(root, set())

    assert _state(storage, placement_id) == "present"
