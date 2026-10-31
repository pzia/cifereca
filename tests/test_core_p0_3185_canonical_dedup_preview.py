"""#3185: preview never treats equal SHA as an authorization to unlink."""
import json

import pytest

from cifereca.core.canonical_dedup_preview import preview_canonical_duplicate
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, rows, store, target,
)


@pytest.fixture
def rig(store, tmp_path):
    root = tmp_path / "EBooks"
    paths, publications = [], []
    for identity, provider, credential, account, filename in (
        ("A", "seller-a", "card-a", "borrower-a", "first.pdf"),
        ("A", "seller-b", "card-b", "borrower-b", "second.pdf"),
        ("B", "seller-c", "card-c", "borrower-c", "foreign.pdf"),
    ):
        source, snapshot = new_source(
            identity, provider=provider, credential=credential, account=account,
        )
        register(store, identity, provider, credential, account=account)
        path = target(tmp_path, identity, filename)
        paths.append(path)
        publications.append(publish(store, root, path, source, snapshot))
    return store, root, paths, publications


def preview(rig, *, candidate=None, keeper=None):
    storage, root, _paths, (first, second, _foreign) = rig
    return preview_canonical_duplicate(
        storage, library_root=root,
        candidate_placement_id=candidate or second.placement_id,
        keeper_placement_id=keeper or first.placement_id,
    )


def test_unreferenced_pair_verified_without_any_write(rig):
    storage, _root, paths, pubs = rig
    original = rows(storage)
    seen = preview(rig)
    assert (seen.status, seen.reason) == (
        "unreferenced", "verified_no_active_references",
    )
    assert seen.may_attempt_retirement is True
    assert (seen.catalogue_references, seen.backup_references,
            seen.other_or_unknown_references) == (0, 0, False)
    assert seen == preview(rig)
    assert rows(storage) == original == (1, 3, 3, 3)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)


def test_cross_profile_does_not_pass_preview_even_with_same_sha(rig):
    storage, _root, paths, (first, second, foreign) = rig
    result = preview(rig, keeper=foreign.placement_id)
    assert (result.status, result.reason) == ("blocked", "scope_mismatch")
    assert result.may_attempt_retirement is False
    assert all(p.exists() for p in paths)


def test_canonical_catalogue_reference_blocks_preview(rig):
    storage, _root, paths, (_first, second, _foreign) = rig
    storage.catalog_entry_put({
        "catalog_entry_id": "catalogue:second",
        "artifact_id": second.artifact_id,
        "placement_id": second.placement_id,
        "identity_id": "A", "provider_id": "seller-b",
        "item_id": "edition", "title": "Synthetic",
        "metadata": {"path": str(paths[1])},
    })
    found = preview(rig)
    assert found.reason == "active_references"
    assert found.catalogue_references == 1
    assert not found.may_attempt_retirement
    assert paths[1].exists()


def test_legacy_backup_path_blocks_preview(rig):
    storage, _root, paths, _pubs = rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,status,updated_at,local_path) VALUES(?,?,?,?,?,?,?)",
            ("seller-b", "card-b", "edition", "Synthetic", "complete", 1,
             str(paths[1])),
        )
    found = preview(rig)
    assert found.reason == "active_references"
    assert found.backup_references == 1
    assert not found.may_attempt_retirement


@pytest.mark.parametrize("dependency", ["ledger", "metadata", "unreadable_json"])
def test_unknown_operational_references_fail_closed(rig, dependency):
    storage, _root, paths, _pubs = rig
    if dependency == "ledger":
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO acquisition_ledger(acquisition_id,provider_id,"
                "credential_id,account_id,item_id,identity_id,status,"
                "metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("pending", "seller-b", "card-b", "borrower-b",
                 "edition", "A", "canonical_receipt_staged",
                 json.dumps({"path": str(paths[1])}), 1, 1),
            )
    elif dependency == "metadata":
        storage.metadata_set("test.other.active.path", str(paths[1]))
    else:
        storage.metadata_set("test.unknown.envelope", "{bad json")
    found = preview(rig)
    assert found.status == "blocked"
    assert found.reason == "active_references"
    assert found.other_or_unknown_references is True
    assert not found.may_attempt_retirement
    assert paths[1].exists()


def test_tampering_or_symlink_never_classifies_unreferenced(rig):
    storage, _root, paths, _pubs = rig
    paths[1].write_bytes(BYTES[:-1] + b"X")
    found = preview(rig)
    assert (found.status, found.reason) == (
        "blocked", "physical_evidence_changed",
    )
    paths[1].unlink()
    paths[1].symlink_to(paths[0])
    found = preview(rig)
    assert found.reason == "physical_evidence_changed"
    assert not found.may_attempt_retirement
    assert storage.placement_for_path(paths[1]) is not None


def test_missing_or_invalid_placement_is_not_an_empty_dependency_set(rig):
    storage, root, paths, (first, second, foreign) = rig
    result = preview_canonical_duplicate(
        storage, library_root=root,
        candidate_placement_id="unknown-placement",
        keeper_placement_id=first.placement_id,
    )
    assert result.reason == "placement_missing"
    assert not result.may_attempt_retirement
    assert preview_canonical_duplicate(
        storage, library_root=root,
        candidate_placement_id=second.placement_id,
        keeper_placement_id=second.placement_id,
    ).reason == "invalid_request"
    assert all(p.exists() for p in paths)
