"""#3719: three independent safe reference relinks can be resumed together.

Synthetic supplier, SQLite v14 and PDF bytes. No unlink, GET, DRM or ADEPT.
"""
import json

import pytest

from cifereca.core.canonical_dedup_prepare import (
    prepare_verified_canonical_duplicate,
)
from cifereca.core.canonical_recovery import _scope, _stage_id
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, rows,
)
from tests.test_core_p0_3185_canonical_backup_relink import rig, backup


def _journal(rig, *, status="complete"):
    storage, _root, paths, publications, _lock = rig
    source, _snapshot = new_source(
        "A", provider="seller-b", credential="card-b",
        account="buyer-b", edition="book",
    )
    stage_id = _stage_id(source, "synthetic-selected-offer")
    checkpoint = {
        "schema": "canonical.completed-delivery.v1",
        "source": _scope(source), "offer_id": "synthetic-selected-offer",
        "path": str(paths[1]), "sha256": SHA,
        "receipt": "synthetic-completed-delivery", "adept_receipt": "",
    }
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger("
            "acquisition_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,status,artifact_id,metadata_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (stage_id, "seller-b", "card-b", "buyer-b", "book", "A",
             status, publications[1].artifact_id, json.dumps(checkpoint), 1, 1),
        )
    return stage_id


def _catalogue(rig):
    storage, _root, paths, publications, _lock = rig
    storage.catalog_entry_put({
        "catalog_entry_id": "catalogue:seller-b",
        "artifact_id": publications[1].artifact_id,
        "placement_id": publications[1].placement_id,
        "identity_id": "A", "provider_id": "seller-b",
        "item_id": "book", "title": "Synthetic two",
        "metadata": {"path": str(paths[1]), "source": "seller-b"},
    })


def _prepare(rig, *, keeper=None, candidate=None):
    storage, root, _paths, pubs, lock_path = rig
    with InterProcessLock(lock_path) as lock:
        return prepare_verified_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=candidate or pubs[1].placement_id,
            keeper_placement_id=keeper or pubs[0].placement_id,
            shared_lock=lock,
        )


def test_three_references_migrate_and_second_prepare_is_idempotent(rig):
    storage, _root, paths, pubs, _lock = rig
    _catalogue(rig)
    backup(rig)
    checkpoint = _journal(rig)
    original = rows(storage)
    before = storage.acquisitions()
    prepared = _prepare(rig)
    assert (prepared.catalogue_relinked, prepared.backup_relinked,
            prepared.receipt_relinked, prepared.remaining_dependencies) == (
                1, 1, 1, False,
            )
    assert prepared.ready_for_separate_retirement_review
    assert storage.catalog_entry_get("catalogue:seller-b")["metadata"]["path"] == str(paths[0])
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[0].placement_id
    assert next(row for row in storage.backup_list()
                if row["credential_id"] == "card-b")["local_path"] == str(paths[0])
    with storage._lock, storage._connect() as db:
        payload = json.loads(db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (checkpoint,),
        ).fetchone()[0])
    assert payload["path"] == str(paths[0])
    assert payload["receipt"] == "synthetic-completed-delivery"
    assert len(storage.acquisitions()) == len(before)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert rows(storage) == original == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)
    retried = _prepare(rig)
    assert (retried.catalogue_relinked, retried.backup_relinked,
            retried.receipt_relinked, retried.remaining_dependencies) == (
                0, 0, 0, False,
            )
    assert all(path.read_bytes() == BYTES for path in paths)


def test_unmigratable_receipt_is_veto_after_safe_prior_relinks(rig):
    storage, _root, paths, pubs, _lock = rig
    _catalogue(rig)
    backup(rig)
    stage_id = _journal(rig, status="canonical_receipt_staged")
    with pytest.raises(ValueError, match="canonical_receipt_relink_checkpoint_conflict"):
        _prepare(rig)
    # Partial progress means safe reference movement, NEVER loss of a file,
    # SourceReference, OriginLink or a pending ADEPT/provenance journal.
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[0].placement_id
    assert next(x for x in storage.backup_list()
                if x["credential_id"] == "card-b")["local_path"] == str(paths[0])
    with storage._lock, storage._connect() as db:
        payload = json.loads(db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (stage_id,),
        ).fetchone()[0])
    assert payload["path"] == str(paths[1])
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert rows(storage) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_unknown_operational_metadata_is_a_postcheck_blocker(rig):
    storage, _root, paths, _pubs, _lock = rig
    storage.metadata_set("synthetic.unmigrated.private.path", str(paths[1]))
    outcome = _prepare(rig)
    assert outcome.remaining_dependencies
    assert not outcome.ready_for_separate_retirement_review
    assert all(path.read_bytes() == BYTES for path in paths)


@pytest.mark.parametrize("condition", ["cross_profile", "altered", "missing"])
def test_pair_must_rehash_within_one_exact_owner_before_changes(rig, condition):
    storage, _root, paths, pubs, _lock = rig
    _catalogue(rig)
    if condition == "altered":
        paths[1].write_bytes(b"synthetic altered PDF")
    elif condition == "missing":
        paths[1].unlink()
    with pytest.raises(ValueError, match="canonical_prepare_pair_unverified"):
        _prepare(rig, keeper=pubs[2].placement_id
                 if condition == "cross_profile" else None)
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[1].placement_id
    assert rows(storage) == (1, 3, 3, 3)


def test_common_lock_is_required_even_when_no_references(rig):
    storage, root, paths, pubs, lock_path = rig
    with pytest.raises(ValueError, match="canonical_prepare_invalid_request"):
        prepare_verified_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            shared_lock=InterProcessLock(lock_path),
        )
    assert all(path.read_bytes() == BYTES for path in paths)
