"""#3185: preview counts artifact provenance, not permission on either path."""
from cifereca.core.canonical_dedup_preview import preview_canonical_duplicate
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3185_canonical_backup_relink import rig, backup
from tests.test_core_p0_3719_prepare_duplicate import _catalogue


def _preview(rig, *, candidate_index=1, keeper_index=0):
    storage, root, _paths, pubs, _lock = rig
    return preview_canonical_duplicate(
        storage, library_root=root,
        candidate_placement_id=pubs[candidate_index].placement_id,
        keeper_placement_id=pubs[keeper_index].placement_id,
    )


def test_verified_duplicate_counts_both_exact_profile_origins_without_mutation(rig):
    storage, _root, paths, pubs, _lock = rig
    before = rows(storage)
    result = _preview(rig)
    assert (result.status, result.reason) == (
        "unreferenced", "verified_no_active_references",
    )
    assert result.provenance_counted is True
    assert result.artifact_origin_links == 2
    assert result.linked_source_references == 2
    # The third origin has identical SHA, but belongs to a different profile.
    assert before == (1, 3, 3, 3)
    assert rows(storage) == before
    assert all(p.read_bytes() == BYTES for p in paths)
    assert result.may_attempt_retirement is True  # Advisory, not consent.


def test_references_block_retirement_without_hiding_artifact_provenance(rig):
    storage, _root, paths, pubs, _lock = rig
    _catalogue(rig)
    backup(rig)
    before = rows(storage)
    result = _preview(rig)
    assert (result.status, result.reason) == ("blocked", "active_references")
    assert result.provenance_counted is True
    assert (result.artifact_origin_links, result.linked_source_references) == (2, 2)
    assert (result.catalogue_references, result.backup_references) == (1, 1)
    assert not result.may_attempt_retirement
    assert rows(storage) == before
    assert all(p.exists() for p in paths)


def test_cross_profile_pair_does_not_disclose_or_infer_provenance(rig):
    storage, _root, paths, pubs, _lock = rig
    before = rows(storage)
    result = _preview(rig, candidate_index=2, keeper_index=0)
    assert (result.status, result.reason) == ("blocked", "scope_mismatch")
    assert result.provenance_counted is False
    assert result.artifact_origin_links == 0
    assert result.linked_source_references == 0
    assert rows(storage) == before
    assert all(p.read_bytes() == BYTES for p in paths)


def test_tampered_bytes_cannot_claim_verified_provenance_snapshot(rig):
    storage, _root, paths, pubs, _lock = rig
    paths[1].write_bytes(BYTES[:-1] + b"X")
    result = _preview(rig)
    assert (result.status, result.reason) == (
        "blocked", "physical_evidence_changed",
    )
    assert result.provenance_counted is False
    assert result.artifact_origin_links == 0
    assert result.linked_source_references == 0
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
