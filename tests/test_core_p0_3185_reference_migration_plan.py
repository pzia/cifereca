"""#3185: historical reference inventory is not deletion consent.

Synthetic SQLite and PDF, no network, DRM, actual user files or file mutation.
"""
from types import SimpleNamespace as NS

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.core.canonical_dedup_reference_plan import (
    plan_verified_canonical_reference_migration,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3185_canonical_backup_relink import rig, backup
from tests.test_core_p0_3719_prepare_duplicate import _catalogue, _journal


def _plan(rig, *, candidate=1, keeper=0):
    storage, root, _paths, pubs, _lock = rig
    return plan_verified_canonical_reference_migration(
        storage, library_root=root,
        candidate_placement_id=pubs[candidate].placement_id,
        keeper_placement_id=pubs[keeper].placement_id,
    )


def test_no_references_plan_is_advisory_and_preserves_all_provenances(rig):
    storage, root, paths, pubs, _lock = rig
    before = rows(storage)
    first = _plan(rig)
    assert first == _plan(rig)
    assert first.status == "review_required"
    assert first.reason == "fresh_supervisor_validation_required"
    assert first.physical_pair_verified_at_preview
    assert (first.artifact_origin_links, first.linked_source_references) == (2, 2)
    assert (first.catalogue_candidates, first.backup_candidates,
            first.completed_receipt_candidates, first.unverified_references) == (
                0, 0, 0, 0,
            )
    assert first.needs_separate_confirmation
    assert rows(storage) == before == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_three_reference_families_are_counted_without_relink_or_unlink(rig):
    storage, root, paths, pubs, _lock = rig
    _catalogue(rig)
    backup(rig)
    receipt = _journal(rig)
    before = rows(storage)
    result = _plan(rig)
    assert result.status == "review_required"
    assert result.physical_pair_verified_at_preview
    assert (result.catalogue_candidates, result.backup_candidates,
            result.completed_receipt_candidates, result.unverified_references) == (
                1, 1, 1, 0,
            )
    assert result.artifact_origin_links == 2
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[1].placement_id
    assert next(x for x in storage.backup_list()
                if x["credential_id"] == "card-b")["local_path"] == str(paths[1])
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT status FROM acquisition_ledger WHERE acquisition_id=?",
            (receipt,),
        ).fetchone() == ("complete",)
    assert rows(storage) == before
    assert all(path.read_bytes() == BYTES for path in paths)


def test_staged_receipt_vetoes_plan_without_file_changes(rig):
    storage, _root, paths, pubs, _lock = rig
    _journal(rig, status="canonical_receipt_staged")
    result = _plan(rig)
    assert result.status == "blocked"
    assert result.reason == "unverified_references"
    assert result.completed_receipt_candidates == 0
    assert result.unverified_references == 1
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)


def test_legacy_backup_without_exact_account_link_is_unverified(rig):
    storage, _root, paths, _pubs, _lock = rig
    backup(rig, account="not-the-published-borrower")
    result = _plan(rig)
    assert result.status == "blocked"
    assert (result.backup_candidates, result.unverified_references) == (0, 1)
    assert paths[1].exists()


def test_opaque_path_vetoes_even_when_other_three_relinks_are_attested(rig):
    storage, _root, paths, _pubs, _lock = rig
    _catalogue(rig)
    backup(rig)
    _journal(rig)
    storage.metadata_set("old.unclassified.operation", str(paths[1]))
    result = _plan(rig)
    assert result.status == "blocked"
    assert result.catalogue_candidates == 1
    assert result.backup_candidates == 1
    assert result.completed_receipt_candidates == 1
    assert result.unverified_references >= 1
    assert storage.metadata_get("old.unclassified.operation") == str(paths[1])


def test_foreign_profile_and_tampered_copy_never_report_migration_evidence(rig):
    storage, _root, paths, pubs, _lock = rig
    foreign = _plan(rig, keeper=2)
    assert foreign.status == "blocked" and foreign.reason == "pair_unverified"
    assert not foreign.physical_pair_verified_at_preview
    assert foreign.artifact_origin_links == 0
    paths[1].write_bytes(BYTES[:-1] + b"X")
    damaged = _plan(rig)
    assert damaged.status == "blocked" and damaged.reason == "pair_unverified"
    assert not damaged.physical_pair_verified_at_preview
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"


def test_broker_plan_uses_configured_root_and_is_read_only(rig):
    storage, root, paths, _pubs, _lock = rig
    service = NS(
        storage=storage,
        config=NS(get=lambda key, default=None: str(root)
                  if key == "library.directory" else default),
    )
    broker = LibraryMaintenanceBrokerImpl(service)
    result = broker.canonical_reference_migration_plan(
        candidate_placement_id=rig[3][1].placement_id,
        keeper_placement_id=rig[3][0].placement_id,
    )
    assert result.status == "review_required"
    assert result.needs_separate_confirmation
    assert all(path.read_bytes() == BYTES for path in paths)
