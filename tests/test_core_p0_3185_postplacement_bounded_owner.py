"""#3185: post-B duplicate probe must scope directory lookup without owner drift.

SQLite+synthetic PDF bytes. Read-only probe, no unlink, acquisition or GET.
"""
from __future__ import annotations

from cifereca.core import canonical_post_placement
from cifereca.core.canonical_post_placement import verified_post_placement_duplicates
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_core_p0_3719_post_placement_duplicates import _two_accounts


def test_many_unrelated_profiles_do_not_affect_same_owner_physical_proof(
    store, tmp_path, monkeypatch,
):
    root, _paths, (first, second, foreign) = _two_accounts(store, tmp_path)
    for n in range(250):
        store.identity_put({
            "identity_id": f"unrelated-{n:03d}",
            "label": f"Unrelated profile {n}",
            "directory_name": f"folder-{n:03d}",
        })
    calls = []
    original = canonical_post_placement._verified_copy

    def probe(path, **kwargs):
        calls.append(kwargs["registered_directories"])
        return original(path, **kwargs)

    monkeypatch.setattr(canonical_post_placement, "_verified_copy", probe)
    before = store.placements()
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) == (second.placement_id,)
    assert calls and all(registered == frozenset({"A"})
                         for registered in calls)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=foreign.placement_id,
    ) == ()
    assert store.placements() == before


def test_corrupt_shared_profile_directory_never_attests_duplicate(
    store, tmp_path,
):
    root, _paths, (first, second, _foreign) = _two_accounts(store, tmp_path)
    before = store.placements()
    # The current v14 UNIQUE index prevents this via identity_put. Simulate
    # an externally edited/legacy database only inside this disposable test.
    with store._lock, store._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
    try:
        store.identity_put({
            "identity_id": "shadow", "label": "Corrupt shadow",
            "directory_name": "A",
        })
        assert verified_post_placement_duplicates(
            store, library_root=root, placement_id=first.placement_id,
        ) == ()
        assert verified_post_placement_duplicates(
            store, library_root=root, placement_id=second.placement_id,
        ) == ()
        assert store.placements() == before
    finally:
        with store._lock, store._connect() as db:
            db.execute("DELETE FROM identities WHERE identity_id='shadow'")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_directory_name "
                "ON identities(directory_name)"
            )


def test_missing_owner_no_longer_attests_even_with_verified_target_bytes(
    store, tmp_path,
):
    root, _paths, (first, second, _foreign) = _two_accounts(store, tmp_path)
    before = store.placements()
    with store._lock, store._connect() as db:
        db.execute("DELETE FROM identities WHERE identity_id='A'")
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) == ()
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=second.placement_id,
    ) == ()
    assert store.placements() == before
