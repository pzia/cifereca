"""P0 #3185: duplicate maintenance preview and postcheck use one exact owner.

Synthetic local PDFs and SQLite; the only physical retirement in this suite
is explicitly invoked on disposable files under the test directory.
"""
from __future__ import annotations

from cifereca.core import canonical_dedup_preview as preview_mod
from cifereca.core import canonical_dedup_supervisor as supervisor
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_core_p0_3719_canonical_duplicate_retirement import rig, _retire


def _pair(rig):
    storage, root, paths, publications, _snapshot, _lock_path = rig
    return storage, root, paths, publications


def _preview(storage, root, pubs):
    return preview_mod.preview_canonical_duplicate(
        storage, library_root=root,
        candidate_placement_id=pubs[1].placement_id,
        keeper_placement_id=pubs[0].placement_id,
    )


def _postcheck(storage, root, paths, pubs):
    return supervisor._verified_postcheck(
        storage, library_root=root,
        candidate_placement_id=pubs[1].placement_id,
        keeper_placement_id=pubs[0].placement_id,
        candidate_path=str(paths[1]),
    )


def _many_profiles(storage, count=225):
    for n in range(count):
        storage.identity_put({
            "identity_id": f"foreign-{n:04d}",
            "label": f"Foreign {n}",
            "directory_name": f"foreign-folder-{n:04d}",
        })


def _corrupt_root(storage):
    # V14 Storage rejects this state through the UNIQUE directory index.
    # Only a disposable SQLite fixture can simulate externally edited data.
    with storage._lock, storage._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
    storage.identity_put({
        "identity_id": "corrupt-shadow", "label": "Synthetic shadow",
        "directory_name": "A",
    })


def _repair_root(storage):
    with storage._lock, storage._connect() as db:
        db.execute(
            "DELETE FROM identities WHERE identity_id='corrupt-shadow'"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_directory_name "
            "ON identities(directory_name)"
        )


def test_preview_uses_exact_owner_with_hundreds_unrelated_profiles(
    rig, monkeypatch,
):
    storage, root, paths, pubs = _pair(rig)
    _many_profiles(storage)
    before = storage.placements()
    observed = []
    actual = preview_mod._verified_copy

    def wrapped(path, **kwargs):
        observed.append(kwargs["registered_directories"])
        return actual(path, **kwargs)

    monkeypatch.setattr(preview_mod, "_verified_copy", wrapped)
    outcome = _preview(storage, root, pubs)
    assert outcome.status == "unreferenced"
    assert outcome.provenance_counted is True
    assert observed == [frozenset({"A"}), frozenset({"A"})]
    assert storage.placements() == before
    assert all(p.is_file() for p in paths)


def test_preview_refuses_corrupt_shared_root_even_with_matching_sha(rig):
    storage, root, paths, pubs = _pair(rig)
    assert _preview(storage, root, pubs).status == "unreferenced"
    before = storage.placements()
    _corrupt_root(storage)
    try:
        outcome = _preview(storage, root, pubs)
        assert (outcome.status, outcome.reason) == (
            "blocked", "profile_ambiguous",
        )
        assert outcome.provenance_counted is False
        assert storage.placements() == before
        assert all(p.is_file() for p in paths)
    finally:
        _repair_root(storage)
    assert _preview(storage, root, pubs).status == "unreferenced"


def test_retirement_postcheck_rehashes_keeper_with_exact_owner_scope(
    rig, monkeypatch,
):
    storage, root, paths, pubs = _pair(rig)
    _many_profiles(storage)
    retired = _retire(rig)
    assert retired.retired_placement and retired.removed_file
    assert not paths[1].exists()
    before = storage.placements()
    observed = []
    actual = supervisor._verified_copy

    def wrapped(path, **kwargs):
        observed.append(kwargs["registered_directories"])
        return actual(path, **kwargs)

    monkeypatch.setattr(supervisor, "_verified_copy", wrapped)
    assert _postcheck(storage, root, paths, pubs) is True
    assert observed == [frozenset({"A"})]
    assert storage.placements() == before


def test_postcheck_refuses_external_duplicate_owner_without_mutation(rig):
    storage, root, paths, pubs = _pair(rig)
    retired = _retire(rig)
    assert retired.retired_placement and retired.removed_file
    assert _postcheck(storage, root, paths, pubs) is True
    before = storage.placements()
    _corrupt_root(storage)
    try:
        assert _postcheck(storage, root, paths, pubs) is False
        assert storage.placements() == before
        assert paths[0].is_file() and not paths[1].exists()
    finally:
        _repair_root(storage)
    assert _postcheck(storage, root, paths, pubs) is True
