"""#3185: SQLite inventory bounds identity lookup and refuses shared roots.

All cases are synthetic, read-only inventory; no filesystem scans, hashes,
deletions, relinks or provider requests.
"""
from __future__ import annotations

from cifereca.core.canonical_placement_inventory import (
    read_canonical_placement_inventory,
)
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_core_p0_3719_post_placement_duplicates import _two_accounts


def _sample(store, tmp_path):
    _two_accounts(store, tmp_path)
    with store._lock, store._connect() as db:
        return {
            name: db.execute("SELECT count(*) FROM " + name).fetchone()[0]
            for name in ("identities", "placements", "artifacts", "origin_links")
        }


def test_inventory_same_profile_with_hundreds_unrelated_identities(store, tmp_path):
    before = _sample(store, tmp_path)
    for idx in range(400):
        store.identity_put({
            "identity_id": f"unrelated-{idx:04d}",
            "label": f"Private synthetic {idx}",
            "directory_name": f"unrelated-root-{idx:04d}",
        })
    before_read = {
        name: len(store.placements()) if name == "placements" else 0
        for name in ("placements",)
    }
    result = read_canonical_placement_inventory(store, identity_id="A")
    assert result.status == "reviewed"
    assert result.placement_count == 2
    assert result.candidate_count == 1
    assert result.blocked_count == 0
    assert len(store.placements()) == before_read["placements"]
    assert before["placements"] == 3


def test_second_identity_claiming_exact_same_root_fails_closed(store, tmp_path):
    _sample(store, tmp_path)
    canonical = store.identity_get("A")
    assert canonical is not None
    store.identity_put({
        "identity_id": "shadow-owner",
        "label": "Shadow synthetic",
        "directory_name": canonical["directory_name"],
    })
    before = store.placements()
    inventory = read_canonical_placement_inventory(store, identity_id="A")
    assert inventory.status == "unavailable"
    assert inventory.groups == ()
    assert store.placements() == before
    # An unrelated directory remains independently inspectable.
    other = read_canonical_placement_inventory(store, identity_id="B")
    assert other.status == "reviewed"
    assert other.placement_count == 1


def test_unknown_profile_cannot_inherit_other_profile_directory(store, tmp_path):
    _sample(store, tmp_path)
    for bogus in ("missing-profile", "", "A\nB"):
        inventory = read_canonical_placement_inventory(store, identity_id=bogus)
        assert inventory.status == "unavailable"
        assert inventory.groups == ()


def test_duplicate_directory_isolation_can_be_restored_without_file_change(store, tmp_path):
    _sample(store, tmp_path)
    directory = store.identity_get("A")["directory_name"]
    store.identity_put({
        "identity_id": "shadow-owner", "label": "Shadow synthetic",
        "directory_name": directory,
    })
    assert read_canonical_placement_inventory(store, identity_id="A").status == "unavailable"
    store.identity_put({
        "identity_id": "shadow-owner", "label": "Shadow synthetic",
        "directory_name": "shadow-distinct-root",
    })
    result = read_canonical_placement_inventory(store, identity_id="A")
    assert result.status == "reviewed"
    assert result.candidate_count == 1
