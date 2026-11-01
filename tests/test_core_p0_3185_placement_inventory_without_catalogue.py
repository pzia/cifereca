"""#3185: the read-only maintenance inventory must not depend on catalogue cards."""
from types import SimpleNamespace

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.core.canonical_placement_inventory import (
    read_canonical_placement_inventory,
)
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_core_p0_3719_post_placement_duplicates import _two_accounts


def _snapshot(store):
    with store._lock, store._connect() as db:
        return tuple(db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                     for table in ("artifacts", "placements", "origin_links",
                                   "source_references", "catalog_entries", "metadata"))


def test_two_present_canonical_placements_without_catalogue_are_discovered(store, tmp_path):
    _root, paths, (one, two, foreign) = _two_accounts(store, tmp_path)
    before = _snapshot(store)
    broker = LibraryMaintenanceBrokerImpl(SimpleNamespace(storage=store))
    inventory = broker.canonical_placement_inventory(identity_id="A")
    assert inventory == broker.canonical_placement_inventory(identity_id="A")
    assert inventory.status == "reviewed"
    assert inventory.placement_count == 2
    assert inventory.candidate_count == 1
    assert inventory.blocked_count == 0
    group, = inventory.groups
    assert group.status == "candidate_review"
    assert group.reason == "physical_and_reference_review_required"
    assert set(row.placement_id for row in group.placements) == {
        one.placement_id, two.placement_id,
    }
    assert all(not row.catalogued for row in group.placements)
    assert foreign.placement_id not in {row.placement_id for row in group.placements}
    assert "B" not in str(inventory)
    assert not any(str(path) in str(inventory) for path in paths)
    assert _snapshot(store) == before


def test_identical_sha_in_second_identity_stays_independent(store, tmp_path):
    _root, _paths, (_one, _two, foreign) = _two_accounts(store, tmp_path)
    inventory = read_canonical_placement_inventory(store, identity_id="B")
    assert inventory.status == "reviewed"
    assert inventory.placement_count == 1
    assert inventory.candidate_count == 0
    assert inventory.groups[0].status == "unique"
    assert inventory.groups[0].placements[0].placement_id == foreign.placement_id


def test_missing_row_does_not_disappear_when_file_is_still_present(store, tmp_path):
    _root, paths, (one, two, _foreign) = _two_accounts(store, tmp_path)
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET state='missing' WHERE placement_id=?",
                   (two.placement_id,))
    before = _snapshot(store)
    result = read_canonical_placement_inventory(store, identity_id="A")
    assert result.status == "partial"
    assert result.placement_count == 2
    assert result.groups[0].status == "blocked"
    assert result.groups[0].reason == "placement_recovery_required"
    assert set(row.placement_id for row in result.groups[0].placements) == {
        one.placement_id, two.placement_id,
    }
    assert all(path.exists() for path in paths)
    assert _snapshot(store) == before


def test_legacy_provider_bound_copy_never_becomes_actionable(store, tmp_path):
    _root, _paths, (_one, two, _foreign) = _two_accounts(store, tmp_path)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id=?,source_item_id=? WHERE placement_id=?",
            ("provider-old", "book-old", two.placement_id),
        )
    result = read_canonical_placement_inventory(store, identity_id="A")
    assert result.status == "partial"
    assert result.groups[0].status == "blocked"
    assert result.groups[0].reason == "legacy_source_scope_unbound"


def test_broken_catalogue_scope_is_blocked_not_hidden(store, tmp_path):
    _root, _paths, (one, _two, _foreign) = _two_accounts(store, tmp_path)
    store.catalog_entry_put({
        "catalog_entry_id": "cross-artifact", "artifact_id": one.artifact_id,
        "placement_id": one.placement_id, "identity_id": "B",
        "title": "Synthetic catalogue cross-identity", "provider_id": "local",
        "item_id": "other",
    })
    result = read_canonical_placement_inventory(store, identity_id="A")
    assert result.status == "partial"
    assert result.groups[0].reason == "catalogue_scope_mismatch"


def test_sql_budget_does_not_pretend_a_truncated_inventory_is_complete(
    store, tmp_path, monkeypatch,
):
    from cifereca.core import canonical_placement_inventory as module
    _two_accounts(store, tmp_path)
    monkeypatch.setattr(module, "_MAX_PLACEMENTS", 1)
    result = module.read_canonical_placement_inventory(store, identity_id="A")
    assert result.status == "over_budget"
    assert result.groups == ()
    assert result.placement_count == 0


def test_invalid_or_unknown_identity_fails_closed(store, tmp_path):
    _two_accounts(store, tmp_path)
    assert read_canonical_placement_inventory(store, identity_id="unknown").status == "unavailable"
    assert read_canonical_placement_inventory(store, identity_id="").status == "unavailable"
    assert read_canonical_placement_inventory(store, identity_id="A\nB").status == "unavailable"
