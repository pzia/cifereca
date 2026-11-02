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
    assert allowed == [frozenset({"A"})]
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
