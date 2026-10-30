"""#3719/#3185: first safe canonical post-placement relink is SQL-only.

All providers, rights and content are synthetic. Both physical copies stay put.
"""
import json
import sqlite3

import pytest

from cifereca.core.canonical_dedup_catalog_relink import (
    relink_canonical_duplicate_catalogue,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, rows, store, target,
)


@pytest.fixture
def rig(store, tmp_path, monkeypatch):
    root = tmp_path / "EBooks"
    source_a, snap_a = new_source(
        "A", provider="seller-a", credential="card-a", account="borrower-a",
    )
    source_b, snap_b = new_source(
        "A", provider="seller-b", credential="card-b", account="borrower-b",
    )
    source_c, snap_c = new_source(
        "B", provider="seller-c", credential="card-c", account="borrower-c",
    )
    register(store, "A", "seller-a", "card-a", account="borrower-a")
    register(store, "A", "seller-b", "card-b", account="borrower-b")
    register(store, "B", "seller-c", "card-c", account="borrower-c")
    pa = target(tmp_path, "A", "first.pdf")
    pb = target(tmp_path, "A", "second.pdf")
    pc = target(tmp_path, "B", "foreign.pdf")
    first = publish(store, root, pa, source_a, snap_a)
    second = publish(store, root, pb, source_b, snap_b)
    foreign = publish(store, root, pc, source_c, snap_c)

    for i, placement, path, provider in (
        (1, first, pa, "seller-a"),
        (2, second, pb, "seller-b"),
    ):
        store.catalog_entry_put({
            "catalog_entry_id": f"catalogue:{i}",
            "artifact_id": placement.artifact_id,
            "placement_id": placement.placement_id,
            "identity_id": "A", "provider_id": provider,
            "item_id": f"edition-{i}", "title": f"edition {i}",
            "metadata": {"path": str(path), "source_label": provider},
        })
    lock_path = tmp_path / "locks" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: lock_path,
    )
    return store, root, (pa, pb, pc), (first, second, foreign), lock_path


def relink(rig, *, candidate=None, keeper=None):
    storage, root, _paths, (first, second, _foreign), lock_path = rig
    with InterProcessLock(lock_path) as lease:
        return relink_canonical_duplicate_catalogue(
            storage, library_root=root,
            candidate_placement_id=candidate or second.placement_id,
            keeper_placement_id=keeper or first.placement_id,
            shared_lock=lease,
        )


def test_catalogue_relink_is_atomic_profile_scoped_and_preserves_provenance(rig):
    storage, _root, paths, (first, second, _foreign), _lock = rig
    before = rows(storage)
    assert before == (1, 3, 3, 3)
    assert relink(rig) == 1
    assert relink(rig) == 0
    direct = storage.catalog_entry_get("catalogue:2")
    assert direct["placement_id"] == first.placement_id
    assert direct["provider_id"] == "seller-b"
    assert direct["item_id"] == "edition-2"
    assert direct["metadata"]["path"] == str(paths[0])
    assert direct["metadata"]["source_label"] == "seller-b"
    assert storage.catalog_entry_get("catalogue:1")["placement_id"] == first.placement_id
    assert storage.placement_get(second.placement_id)["state"] == "present"
    assert rows(storage) == before
    assert all(path.read_bytes() == BYTES for path in paths)


def test_cross_profile_or_changed_bytes_refuses_catalogue_relink(rig):
    storage, _root, paths, (first, second, foreign), _lock = rig
    with pytest.raises(ValueError, match="canonical_relink_scope_changed"):
        relink(rig, keeper=foreign.placement_id)
    paths[0].write_bytes(BYTES[:-1] + b"X")
    with pytest.raises(ValueError, match="canonical_relink_physical_evidence_changed"):
        relink(rig)
    assert storage.catalog_entry_get("catalogue:2")["placement_id"] == second.placement_id
    assert all(path.exists() for path in paths)


def test_nested_path_dependency_fails_closed_without_partial_relink(rig):
    storage, _root, paths, (_first, second, _foreign), _lock = rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE catalog_entries SET metadata_json=? WHERE catalog_entry_id=?",
            (json.dumps({"path": str(paths[1]), "receipt": {
                "private_original_path": str(paths[1]),
            }}), "catalogue:2"),
        )
    with pytest.raises(ValueError, match="canonical_relink_nested_path_dependency"):
        relink(rig)
    assert storage.catalog_entry_get("catalogue:2")["placement_id"] == second.placement_id


def test_sql_failure_rolls_back_all_catalogue_rebindings(rig):
    storage, _root, paths, (_first, second, _foreign), _lock = rig
    storage.catalog_entry_put({
        "catalog_entry_id": "catalogue:3",
        "artifact_id": second.artifact_id,
        "placement_id": second.placement_id, "identity_id": "A",
        "provider_id": "seller-b", "item_id": "edition-3",
        "title": "alternative", "metadata": {"path": str(paths[1])},
    })
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER fail_relink BEFORE UPDATE OF placement_id "
            "ON catalog_entries WHEN NEW.catalog_entry_id='catalogue:3' "
            "BEGIN SELECT RAISE(ABORT,'synthetic_relink_failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_relink_failure"):
        relink(rig)
    assert all(storage.catalog_entry_get(f"catalogue:{i}")["placement_id"]
               == second.placement_id for i in (2, 3))
    assert all(path.read_bytes() == BYTES for path in paths)


def test_relink_requires_live_acquisition_lock(rig):
    storage, root, paths, (first, second, _foreign), lock_path = rig
    with pytest.raises(ValueError, match="canonical_relink_invalid_request"):
        relink_canonical_duplicate_catalogue(
            storage, library_root=root,
            candidate_placement_id=second.placement_id,
            keeper_placement_id=first.placement_id,
            shared_lock=InterProcessLock(lock_path),
        )
    assert storage.catalog_entry_get("catalogue:2")["placement_id"] == second.placement_id
    assert paths[1].is_file()
