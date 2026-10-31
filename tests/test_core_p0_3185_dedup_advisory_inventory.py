"""#3185: post-placement advisory queue inspection is bounded and read-only."""
import json
from types import SimpleNamespace

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.core.canonical_dedup_advisory_review import (
    review_canonical_dedup_advisories,
)
from cifereca.core.canonical_post_placement import (
    checkpoint_post_placement_duplicates_change,
    verified_post_placement_duplicates,
)
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_core_p0_3719_post_placement_duplicates import _two_accounts


def _checkpoint(store, tmp_path):
    root, paths, (one, two, foreign) = _two_accounts(store, tmp_path)
    candidates = verified_post_placement_duplicates(
        store, library_root=root, placement_id=one.placement_id,
    )
    assert candidates == (two.placement_id,)
    key, changed = checkpoint_post_placement_duplicates_change(
        store, placement_id=one.placement_id,
        duplicate_placement_ids=candidates,
    )
    assert changed
    return key, (one, two, foreign)


def test_inventory_exact_distinct_checkpoint_and_profile_scope(store, tmp_path):
    key, (one, two, foreign) = _checkpoint(store, tmp_path)
    original = store.metadata_get(key)
    broker = LibraryMaintenanceBrokerImpl(SimpleNamespace(storage=store))
    first = broker.pending_canonical_advisories(identity_id="A")
    again = broker.pending_canonical_advisories(identity_id="A")
    assert first == again
    assert first.status == "reviewed"
    assert first.pending_count == 1 and first.blocked_count == 0
    assert len(first.entries) == 1
    row = first.entries[0]
    assert row.checkpoint_key == key
    assert row.identity_id == "A"
    assert set(row.placement_ids) == {one.placement_id, two.placement_id}
    assert row.status == "pending_preview"
    assert row.reason == "physical_review_required"
    assert review_canonical_dedup_advisories(
        store, identity_id="B",
    ).entries == ()
    assert foreign.placement_id not in row.placement_ids
    assert store.metadata_get(key) == original
    assert len(store.placements()) == 3


def test_stale_candidate_is_blocked_without_removing_checkpoint(store, tmp_path):
    key, (_one, two, _foreign) = _checkpoint(store, tmp_path)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET state='missing' WHERE placement_id=?",
            (two.placement_id,),
        )
    observed = review_canonical_dedup_advisories(store, identity_id="A")
    assert observed.status == "partial"
    assert observed.pending_count == 0 and observed.blocked_count == 1
    assert observed.entries[0].reason == "placement_record_changed"
    assert store.metadata_get(key) is not None


def test_corrupt_advisory_never_counts_as_actionable(store, tmp_path):
    key, _ = _checkpoint(store, tmp_path)
    store.metadata_set(key, '{"schema":"future-format","schema":"duplicate"}')
    all_rows = review_canonical_dedup_advisories(store)
    assert all_rows.status == "partial" and all_rows.blocked_count == 1
    assert all_rows.entries[0].reason == "checkpoint_invalid"
    assert all_rows.entries[0].placement_ids == ()
    # An opaque malformed record cannot be attributed to a specific profile.
    scoped = review_canonical_dedup_advisories(store, identity_id="A")
    assert scoped.entries == ()
    assert scoped.status == "partial"  # Unknown owner is not a clean queue.
    assert store.metadata_get(key).count('"schema"') == 2


def test_inventory_budget_fails_closed_without_partial_list(store):
    from cifereca.core.canonical_post_placement import _PENDING_PREFIX
    with store._lock, store._connect() as db:
        db.executemany(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            [(_PENDING_PREFIX + str(index), "{}") for index in range(251)],
        )
    result = review_canonical_dedup_advisories(store)
    assert result.status == "over_budget" and result.entries == ()
    assert result.pending_count == result.blocked_count == 0


def test_cross_identity_candidate_is_not_misclassified_as_usable(store, tmp_path):
    key, (one, two, foreign) = _checkpoint(store, tmp_path)
    malformed = json.loads(store.metadata_get(key))
    malformed["placement_ids"] = sorted([one.placement_id, foreign.placement_id])
    store.metadata_set(key, json.dumps(malformed, sort_keys=True))
    view = review_canonical_dedup_advisories(store, identity_id="A")
    assert view.status == "partial" and view.blocked_count == 1
    assert view.entries[0].reason == "placement_record_changed"
    assert store.placement_get(two.placement_id)["state"] == "present"


def test_scoped_invalid_owner_cannot_report_verified_empty_queue(store, tmp_path):
    key, _ = _checkpoint(store, tmp_path)
    store.metadata_set(key, '{"schema":"unknown","identity_id":null}')
    scoped = review_canonical_dedup_advisories(store, identity_id="A")
    assert scoped.entries == ()
    assert scoped.status == "partial"
    assert scoped.pending_count == 0
    assert store.metadata_get(key) is not None


def test_legacy_provider_bound_placement_never_becomes_canonical_preview(
    store, tmp_path,
):
    key, (_one, candidate, _foreign) = _checkpoint(store, tmp_path)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id=?,source_item_id=? "
            "WHERE placement_id=?",
            ("seller-legacy", "book-legacy", candidate.placement_id),
        )
    scoped = review_canonical_dedup_advisories(store, identity_id="A")
    assert scoped.status == "partial"
    assert scoped.pending_count == 0 and scoped.blocked_count == 1
    assert scoped.entries[0].status == "blocked"
    assert scoped.entries[0].reason == "legacy_placement_requires_review"
    assert store.metadata_get(key) is not None
    # An advisory read must never mutate a legacy ownership record.
    row = store.placement_get(candidate.placement_id)
    assert row["provider_id"] == "seller-legacy"
    assert row["source_item_id"] == "book-legacy"


def test_regular_other_profile_queue_is_not_a_scoped_uncertainty(
    store, tmp_path,
):
    key, (_one, _two, _foreign) = _checkpoint(store, tmp_path)
    assert store.metadata_get(key)
    scoped = review_canonical_dedup_advisories(store, identity_id="B")
    assert scoped.status == "reviewed"
    assert scoped.entries == ()


def test_syntactically_valid_forged_owner_cannot_hide_an_a_checkpoint(
    store, tmp_path,
):
    key, _ = _checkpoint(store, tmp_path)
    payload = json.loads(store.metadata_get(key))
    payload["identity_id"] = "B"  # Valid identity string, wrong key scope.
    store.metadata_set(key, json.dumps(payload, sort_keys=True))

    owner_a = review_canonical_dedup_advisories(store, identity_id="A")
    assert owner_a.status == "partial"
    assert owner_a.entries == ()  # Do not disclose B's claimed payload to A.
    owner_b = review_canonical_dedup_advisories(store, identity_id="B")
    assert owner_b.status == "partial"
    assert owner_b.blocked_count == 1
    assert owner_b.entries[0].reason == "checkpoint_scope_mismatch"
    assert json.loads(store.metadata_get(key))["identity_id"] == "B"


def test_wrong_artifact_fingerprint_with_foreign_owner_is_unattributable(
    store, tmp_path,
):
    key, _ = _checkpoint(store, tmp_path)
    payload = json.loads(store.metadata_get(key))
    payload["identity_id"] = "B"
    payload["artifact_id"] = "sha256:" + "c" * 64
    store.metadata_set(key, json.dumps(payload, sort_keys=True))
    scoped = review_canonical_dedup_advisories(store, identity_id="A")
    assert scoped.status == "partial"
    assert scoped.entries == ()
    assert store.metadata_get(key) is not None
