"""#3185: legacy provider-bound placements cannot be adopted from SHA alone."""
from types import SimpleNamespace

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.core.canonical_legacy_scope_review import (
    review_legacy_placement_scope,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, rows, store, target,
)
from tests.test_core_p0_3185_canonical_backup_relink import rig


def _mark_legacy(rig, *, index=1, provider="seller-b", edition="book"):
    storage, _root, _paths, pubs, _lock = rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id=?,source_item_id=? "
            "WHERE placement_id=?",
            (provider, edition, pubs[index].placement_id),
        )


def _review(rig, *, candidate=1, keeper=0):
    storage, root, _paths, pubs, _lock = rig
    return review_legacy_placement_scope(
        storage, library_root=root,
        candidate_placement_id=pubs[candidate].placement_id,
        keeper_placement_id=pubs[keeper].placement_id,
    )


def test_one_recorded_origin_does_not_bind_account_to_legacy_file(rig):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    before = rows(storage)
    result = _review(rig)
    assert result == _review(rig)
    assert result.status == "blocked"
    assert result.reason == "legacy_file_source_unbound"
    assert result.physical_pair_verified_at_review
    assert (result.matching_provider_origin_links,
            result.matching_borrower_scopes) == (1, 1)
    assert result.may_migrate_or_retire is False
    assert rows(storage) == before == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)
    assert storage.placement_get(pubs[1].placement_id)["provider_id"] == "seller-b"


def test_unknown_legacy_provider_has_no_attested_origin(rig):
    storage, _root, paths, _pubs, _lock = rig
    _mark_legacy(rig, provider="unbound-history")
    result = _review(rig)
    assert result.status == "blocked" and result.reason == "origin_unverified"
    assert result.physical_pair_verified_at_review
    assert result.matching_provider_origin_links == 0
    assert result.matching_borrower_scopes == 0
    assert paths[1].exists()


def test_two_recorded_borrowers_do_not_authorize_file_adoption(rig, tmp_path):
    storage, root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    register(
        storage, "A", "seller-b", "card-b-second", account="buyer-second",
    )
    source, snapshot = new_source(
        "A", provider="seller-b", credential="card-b-second",
        account="buyer-second", edition="book",
    )
    third = target(tmp_path, "A", "third.pdf")
    extra = publish(storage, root, third, source, snapshot)
    assert extra.artifact_id == pubs[1].artifact_id
    result = _review(rig)
    assert result.status == "blocked"
    assert result.reason == "multiple_borrower_scopes"
    assert result.physical_pair_verified_at_review
    assert (result.matching_provider_origin_links,
            result.matching_borrower_scopes) == (2, 2)
    assert result.may_migrate_or_retire is False
    assert third.read_bytes() == paths[0].read_bytes() == BYTES
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"


@pytest.mark.parametrize("provider,edition", [
    ("seller-b", None), (None, "book"), ("", "book"),
])
def test_incomplete_legacy_coordinates_are_blocked(rig, provider, edition):
    _mark_legacy(rig, provider=provider, edition=edition)
    result = _review(rig)
    assert result.status == "blocked"
    assert result.reason == "legacy_scope_incomplete"
    assert not result.physical_pair_verified_at_review


def test_foreign_identity_is_never_eligible_even_on_same_sha(rig):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig, index=2, provider="seller-c", edition="book")
    result = _review(rig, candidate=2)
    assert result.status == "blocked" and result.reason == "scope_mismatch"
    assert not result.physical_pair_verified_at_review
    assert result.matching_provider_origin_links == 0
    assert all(path.read_bytes() == BYTES for path in paths)


def test_tampered_copy_or_symlink_never_qualifies_as_physical_evidence(
    rig, tmp_path,
):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    paths[1].write_bytes(BYTES[:-1] + b"X")
    damaged = _review(rig)
    assert damaged.reason == "physical_unverified"
    assert not damaged.physical_pair_verified_at_review
    paths[1].unlink()
    paths[1].symlink_to(paths[0])
    symlink = _review(rig)
    assert symlink.reason == "physical_unverified"
    assert not symlink.physical_pair_verified_at_review
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert paths[0].read_bytes() == BYTES


def test_canonical_pair_and_legacy_keeper_cannot_use_legacy_review(rig):
    storage, _root, paths, _pubs, _lock = rig
    assert _review(rig).reason == "not_legacy"
    _mark_legacy(rig, index=0, provider="seller-a", edition="book")
    _mark_legacy(rig)
    assert _review(rig).reason == "keeper_not_canonical"
    assert all(path.read_bytes() == BYTES for path in paths)


def test_broker_uses_configured_root_without_exposing_path_or_account(rig):
    storage, root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    service = SimpleNamespace(
        storage=storage,
        config=SimpleNamespace(
            get=lambda key, default=None: (
                str(root) if key == "library.directory" else default
            ),
        ),
    )
    review = LibraryMaintenanceBrokerImpl(
        service,
    ).legacy_placement_scope_review(
        candidate_placement_id=pubs[1].placement_id,
        keeper_placement_id=pubs[0].placement_id,
    )
    assert review.reason == "legacy_file_source_unbound"
    assert review.may_migrate_or_retire is False
    text = repr(review)
    assert str(paths[1]) not in text
    assert "buyer-b" not in text
    assert "card-b" not in text
