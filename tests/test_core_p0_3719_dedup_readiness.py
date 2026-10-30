"""#3719: a checkpoint review is diagnostic, never a destructive grant."""
import json

from cifereca.core.canonical_post_placement import (
    checkpoint_post_placement_duplicates, verified_post_placement_duplicates,
)
from cifereca.core.canonical_duplicate_readiness import (
    review_pending_canonical_duplicates,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, rows, store, target,
)


def _setup(store, tmp_path):
    root = tmp_path / "EBooks"
    a, sa = new_source("A", provider="shop-a", credential="card-a", account="one")
    b, sb = new_source("A", provider="shop-b", credential="card-b", account="two")
    c, sc = new_source("B", provider="shop-c", credential="card-c", account="three")
    register(store, "A", "shop-a", "card-a", account="one")
    register(store, "A", "shop-b", "card-b", account="two")
    register(store, "B", "shop-c", "card-c", account="three")
    paths = [target(tmp_path, "A", "one.pdf"),
             target(tmp_path, "A", "two.pdf"),
             target(tmp_path, "B", "three.pdf")]
    placements = [publish(store, root, path, source, snap)
                  for path, source, snap in zip(paths, (a, b, c), (sa, sb, sc))]
    return root, paths, placements


def _checkpoint(store, root, placement):
    matches = verified_post_placement_duplicates(
        store, library_root=root, placement_id=placement.placement_id,
    )
    return checkpoint_post_placement_duplicates(
        store, placement_id=placement.placement_id,
        duplicate_placement_ids=matches,
    )


def test_readonly_review_marks_only_unreferenced_same_profile_candidate(store, tmp_path):
    root, paths, (first, second, foreign) = _setup(store, tmp_path)
    assert review_pending_canonical_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ).status == "absent"
    _checkpoint(store, root, first)
    before = rows(store)
    result = review_pending_canonical_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert result.status == "reviewed"
    assert [(c.placement_id, c.state) for c in result.candidates] == [
        (second.placement_id, "unreferenced"),
    ]
    assert result.candidates[0].unreferenced is True
    assert rows(store) == before == (1, 3, 3, 3)
    assert all(p.read_bytes() == BYTES for p in paths)
    assert foreign.placement_id not in [c.placement_id for c in result.candidates]


def test_active_path_in_acquisition_ledger_blocks_unreferenced_preview(store, tmp_path):
    root, paths, (first, second, _) = _setup(store, tmp_path)
    _checkpoint(store, root, first)
    store.acquisition_put({
        "provider_id": "shop-b", "credential_id": "card-b",
        "identity_id": "A", "account_id": "two", "item_id": "edition",
        "status": "complete", "metadata": {"receipt": {"path": str(paths[1])}},
    })
    review = review_pending_canonical_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert [(c.placement_id, c.state) for c in review.candidates] == [
        (second.placement_id, "references_need_migration"),
    ]
    assert rows(store) == (1, 3, 3, 3)
    assert all(p.exists() for p in paths)


def test_physical_tampering_or_legacy_source_never_gets_a_green_review(
    store, tmp_path,
):
    root, paths, (first, second, _) = _setup(store, tmp_path)
    _checkpoint(store, root, first)
    paths[1].write_bytes(b"replacement with wrong SHA")
    review = review_pending_canonical_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert review.candidates[0].state == "physical_unverified"
    paths[1].write_bytes(BYTES)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id='legacy-provider' WHERE placement_id=?",
            (second.placement_id,),
        )
    review = review_pending_canonical_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert review.candidates[0].state == "legacy_scope"
    assert paths[1].read_bytes() == BYTES


def test_forged_cross_profile_checkpoint_is_not_permission(store, tmp_path):
    root, paths, (first, second, foreign) = _setup(store, tmp_path)
    key = _checkpoint(store, root, first)
    payload = json.loads(store.metadata_get(key))
    payload["placement_ids"] = [first.placement_id, foreign.placement_id]
    store.metadata_set(key, json.dumps(payload))
    review = review_pending_canonical_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert review.status == "reviewed"
    assert len(review.candidates) == 1
    assert review.candidates[0].state == "changed"
    assert rows(store) == (1, 3, 3, 3)
    assert all(p.read_bytes() == BYTES for p in paths)


def test_malformed_checkpoint_is_stale_and_changes_nothing(store, tmp_path):
    root, paths, (first, _, _) = _setup(store, tmp_path)
    key = _checkpoint(store, root, first)
    store.metadata_set(key, '{"schema":"forged", "schema":"other"}')
    result = review_pending_canonical_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    assert result.status == "stale"
    assert all(p.read_bytes() == BYTES for p in paths)
