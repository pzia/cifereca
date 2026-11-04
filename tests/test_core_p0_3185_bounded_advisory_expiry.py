"""P0 #3185: advisory expiry is read-only until a unique owner proves cleanup.

Physical mutations here occur exclusively on synthetic disposable fixtures.
"""
from __future__ import annotations

from cifereca.core import canonical_post_placement as module
from cifereca.core.convergence_execution import _check_post_placement_duplicates
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, store
from tests.test_core_p0_3719_post_placement_duplicates import _two_accounts


def _stale_checkpoint(store, tmp_path):
    root, paths, (first, second, foreign) = _two_accounts(store, tmp_path)
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    with store._lock, store._connect() as db:
        key, = db.execute(
            "SELECT key FROM metadata WHERE key LIKE "
            "'core.canonical-dedup-pending.v1.%'"
        ).fetchone()
        db.execute(
            "UPDATE placements SET state='missing' WHERE placement_id=?",
            (second.placement_id,),
        )
    paths[1].unlink()  # Simulate explicitly completed synthetic cleanup.
    assert store.metadata_get(key) is not None
    return root, paths, first, foreign, key


def test_expiry_uses_only_owner_directory_under_many_unrelated_profiles(
    store, tmp_path, monkeypatch,
):
    root, paths, first, foreign, key = _stale_checkpoint(store, tmp_path)
    for i in range(240):
        store.identity_put({
            "identity_id": f"unrelated-{i:03d}",
            "label": f"Unrelated {i}",
            "directory_name": f"unrelated-dir-{i:03d}",
        })
    original = module._verified_copy
    allowed = []

    def probe(path, **kwargs):
        allowed.append(kwargs["registered_directories"])
        return original(path, **kwargs)

    monkeypatch.setattr(module, "_verified_copy", probe)
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is True
    assert allowed == [frozenset({"A"}), frozenset({"A"})]
    assert store.metadata_get(key) is None
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    assert store.placement_get(foreign.placement_id)["state"] == "present"


def test_corrupt_shared_owner_never_erases_existing_advisory(
    store, tmp_path,
):
    root, paths, first, foreign, key = _stale_checkpoint(store, tmp_path)
    original = store.metadata_get(key)
    with store._lock, store._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
    store.identity_put({
        "identity_id": "shadow", "label": "Corrupt synthetic shadow",
        "directory_name": "A",
    })
    try:
        assert module.clear_checkpoint_if_no_duplicates(
            store, library_root=root, placement_id=first.placement_id,
        ) is False
        assert store.metadata_get(key) == original
        assert paths[0].read_bytes() == BYTES
    finally:
        with store._lock, store._connect() as db:
            db.execute("DELETE FROM identities WHERE identity_id='shadow'")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_directory_name "
                "ON identities(directory_name)"
            )
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is True
    assert store.metadata_get(key) is None


def test_missing_owner_cannot_discard_stale_marker(
    store, tmp_path,
):
    root, paths, first, _foreign, key = _stale_checkpoint(store, tmp_path)
    before = store.metadata_get(key)
    with store._lock, store._connect() as db:
        db.execute("DELETE FROM identities WHERE identity_id='A'")
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is False
    assert store.metadata_get(key) == before
    assert paths[0].read_bytes() == BYTES


def test_owner_collision_between_hash_and_metadata_delete_never_expires_marker(
    store, tmp_path, monkeypatch,
):
    root, paths, first, _foreign, key = _stale_checkpoint(store, tmp_path)
    original = store.metadata_get(key)
    with store._lock, store._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
    real = module._verified_copy
    observed = []

    def rehash_then_rebind(path, **kwargs):
        assert real(path, **kwargs)
        observed.append(path)
        store.identity_put({
            "identity_id": "shadow", "label": "Synthetic concurrent alias",
            "directory_name": "A",
        })
        return True

    monkeypatch.setattr(module, "_verified_copy", rehash_then_rebind)
    try:
        assert module.clear_checkpoint_if_no_duplicates(
            store, library_root=root, placement_id=first.placement_id,
        ) is False
        assert len(observed) == 1
        assert store.metadata_get(key) == original
        assert paths[0].read_bytes() == BYTES
    finally:
        with store._lock, store._connect() as db:
            db.execute("DELETE FROM identities WHERE identity_id='shadow'")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_directory_name "
                "ON identities(directory_name)"
            )



def test_keeper_corrupted_after_initial_hash_cannot_erase_pending_advisory(
    store, tmp_path, monkeypatch,
):
    root, paths, first, _foreign, key = _stale_checkpoint(store, tmp_path)
    original_marker = store.metadata_get(key)
    assert original_marker is not None
    real = module._verified_copy
    probes = []

    def corrupt_after_first_proof(path, **kwargs):
        verified = real(path, **kwargs)
        probes.append(verified)
        if len(probes) == 1:
            # Synthetic external filesystem writer runs between the initial
            # unlocked SHA proof and the final BEGIN IMMEDIATE cleanup.
            assert verified
            paths[0].write_bytes(b"X" * len(BYTES))
        return verified

    with monkeypatch.context() as patch:
        patch.setattr(module, "_verified_copy", corrupt_after_first_proof)
        assert module.clear_checkpoint_if_no_duplicates(
            store, library_root=root, placement_id=first.placement_id,
        ) is False

    assert probes == [True, False]
    assert store.metadata_get(key) == original_marker
    assert store.placement_get(first.placement_id)["state"] == "present"
    assert paths[0].read_bytes() != BYTES
    # Fixing the synthetic corruption allows a fresh verified expiry, with
    # unchanged SQL history and no destructive file operation by Core.
    paths[0].write_bytes(BYTES)
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is True
    assert store.metadata_get(key) is None
    assert paths[0].read_bytes() == BYTES



def _pending_candidate_id(store):
    with store._lock, store._connect() as db:
        rows = db.execute(
            "SELECT placement_id FROM placements "
            "WHERE identity_id='A' AND state='missing'"
        ).fetchall()
    assert len(rows) == 1
    return rows[0][0]


def test_advisory_not_expired_if_former_path_now_points_outside_owner(
    store, tmp_path,
):
    root, paths, first, foreign, key = _stale_checkpoint(store, tmp_path)
    original = store.metadata_get(key)
    assert paths[2].is_file()
    # An externally edited/migrated row can claim a path under profile B.
    # lexists(False) at that path never proved that profile A's duplicate
    # was retired.
    forged = paths[2].parent / "not-an-A-book.epub"
    assert not forged.exists()
    pending = _pending_candidate_id(store)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET path=? WHERE placement_id=?",
            (str(forged), pending),
        )
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is False
    assert store.metadata_get(key) == original
    assert paths[0].read_bytes() == BYTES
    assert store.placement_get(foreign.placement_id)["state"] == "present"


def test_symlinked_former_parent_is_not_proof_of_absence(
    store, tmp_path,
):
    root, paths, first, _foreign, key = _stale_checkpoint(store, tmp_path)
    original = store.metadata_get(key)
    alias = paths[1].parent / "late-alias"
    alias.symlink_to(paths[2].parent, target_is_directory=True)
    forged = alias / "unseen.epub"
    assert not forged.exists()
    pending = _pending_candidate_id(store)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET path=? WHERE placement_id=?",
            (str(forged), pending),
        )
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is False
    assert store.metadata_get(key) == original
    assert alias.is_symlink()


def test_missing_former_parent_is_not_verified_leaf_absence(
    store, tmp_path,
):
    root, paths, first, _foreign, key = _stale_checkpoint(store, tmp_path)
    original = store.metadata_get(key)
    missing_parent = paths[1].parent / "deleted-directory"
    assert not missing_parent.exists()
    pending = _pending_candidate_id(store)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET path=? WHERE placement_id=?",
            (str(missing_parent / "unseen.epub"), pending),
        )
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is False
    assert store.metadata_get(key) == original



def test_expiry_refuses_original_candidate_while_retirement_tombstone_pending(
    store, tmp_path,
):
    """Missing leaf is not proof that a pending physical cleanup is finalized."""
    root, paths, first, _foreign, key = _stale_checkpoint(store, tmp_path)
    candidate = _pending_candidate_id(store)
    previous = store.metadata_get(key)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET state='dedup_cleanup_pending' "
            "WHERE placement_id=?", (candidate,),
        )
    assert not paths[1].exists()
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is False
    assert store.metadata_get(key) == previous
    assert store.placement_get(candidate)["state"] == "dedup_cleanup_pending"
    assert paths[0].read_bytes() == BYTES

    # Simulate an independently completed supervised recovery that has
    # conclusively disposed of its tombstone. No file needs re-downloading.
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET state='missing' WHERE placement_id=?",
            (candidate,),
        )
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is True
    assert store.metadata_get(key) is None


def test_expiry_refuses_another_unlisted_tombstone_for_same_profile_artifact(
    store, tmp_path,
):
    """A pair-only advisory cannot mark cleanup complete for a third copy."""
    root, paths, first, _foreign, key = _stale_checkpoint(store, tmp_path)
    original = store.metadata_get(key)
    old_path = root / "A" / "another-canonical-copy.pdf"
    assert old_path.parent.is_dir() and not old_path.exists()
    third_id = store.placement_put({
        "identity_id": "A",
        "artifact_id": first.artifact_id,
        "path": str(old_path),
        "provider_id": None,
        "source_item_id": None,
        "state": "dedup_cleanup_pending",
    })
    before = store.placement_get(third_id)
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is False
    assert store.metadata_get(key) == original
    assert store.placement_get(third_id) == before
    assert paths[0].read_bytes() == BYTES

    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET state='missing' WHERE placement_id=?",
            (third_id,),
        )
    assert module.clear_checkpoint_if_no_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) is True
    assert store.metadata_get(key) is None
    assert store.placement_get(third_id)["state"] == "missing"
