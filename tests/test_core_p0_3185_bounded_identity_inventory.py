"""#3185: SQLite inventory bounds identity lookup and refuses shared roots.

All cases are synthetic, read-only inventory; no filesystem scans, hashes,
deletions, relinks or provider requests.
"""
from __future__ import annotations

import sqlite3

import pytest

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


def _inject_corrupt_duplicate_root(store, *, owner="A"):
    """Simulate a legacy/corrupt DB by removing UNIQUE only in a test fixture.

    Live Storage.identity_put MUST reject this state. The reader still
    fails closed when opening an older or externally modified SQLite file.
    """
    root = store.identity_get(owner)["directory_name"]
    with store._lock, store._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
    store.identity_put({
        "identity_id": "shadow-owner", "label": "Synthetic shadow",
        "directory_name": root,
    })


def _restore_unique_root(store):
    with store._lock, store._connect() as db:
        db.execute("DELETE FROM identities WHERE identity_id='shadow-owner'")
        db.execute(
            "CREATE UNIQUE INDEX uq_identity_directory_name "
            "ON identities(directory_name)"
        )


def test_normal_storage_rejects_duplicate_directory_before_inventory(store, tmp_path):
    _sample(store, tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.identity_put({
            "identity_id": "shadow-owner", "label": "Cannot share root",
            "directory_name": store.identity_get("A")["directory_name"],
        })
    assert read_canonical_placement_inventory(
        store, identity_id="A",
    ).status == "reviewed"


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
    _inject_corrupt_duplicate_root(store)
    try:
        before = store.placements()
        inventory = read_canonical_placement_inventory(store, identity_id="A")
        assert inventory.status == "unavailable"
        assert inventory.groups == ()
        assert store.placements() == before
        # An unrelated directory remains independently inspectable.
        other = read_canonical_placement_inventory(store, identity_id="B")
        assert other.status == "reviewed"
        assert other.placement_count == 1
    finally:
        _restore_unique_root(store)


def test_unknown_profile_cannot_inherit_other_profile_directory(store, tmp_path):
    _sample(store, tmp_path)
    for bogus in ("missing-profile", "", "A\nB"):
        inventory = read_canonical_placement_inventory(store, identity_id=bogus)
        assert inventory.status == "unavailable"
        assert inventory.groups == ()


def test_duplicate_directory_isolation_can_be_restored_without_file_change(store, tmp_path):
    _sample(store, tmp_path)
    _inject_corrupt_duplicate_root(store)
    try:
        assert read_canonical_placement_inventory(
            store, identity_id="A",
        ).status == "unavailable"
        store.identity_put({
            "identity_id": "shadow-owner", "label": "Shadow synthetic",
            "directory_name": "shadow-distinct-root",
        })
        # Repair the test-only schema and confirm physical inventory stays
        # independent from the unrelated second profile.
        with store._lock, store._connect() as db:
            db.execute(
                "CREATE UNIQUE INDEX uq_identity_directory_name "
                "ON identities(directory_name)"
            )
        result = read_canonical_placement_inventory(store, identity_id="A")
        assert result.status == "reviewed"
        assert result.candidate_count == 1
    finally:
        with store._lock, store._connect() as db:
            db.execute("DELETE FROM identities WHERE identity_id='shadow-owner'")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_directory_name "
                "ON identities(directory_name)"
            )
