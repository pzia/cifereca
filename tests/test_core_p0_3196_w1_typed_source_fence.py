"""P0 #3196 W1: typed canonical sources must never receive v0.7 replay.

Disposable SQLite only. For a typed Kobo source the old backup projection
becomes a read-only historical archive: no new row or compatibility write
may create/downgrade either the archive or Core rights/journal/placements.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


@pytest.fixture
def storage(tmp_path):
    store = Storage(tmp_path / "library.sqlite3")
    store.identity_put({
        "identity_id": "alice", "directory_name": "alice", "label": "Alice",
    })
    try:
        yield store
    finally:
        store.close()


def _claim(*, item="edition", revision="revision-one", status="complete"):
    return {
        "provider_id": "kobo", "credential_id": "credential",
        "identity_id": "alice", "account_id": "buyer",
        "item_id": item, "revision_id": revision,
        "entitlement_type": "owned", "status": status,
        "title": "Synthetic legacy title",
        "sha256": "a" * 64, "size": 42,
    }


def _source(store, *, edition="edition", account="buyer", owner="alice"):
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("source:" + edition, owner, "kobo", "credential",
             account, "purchases", edition, "purchase", "purchase", 1, 1),
        )


def _canonical(store):
    with store._lock, store._connect() as db:
        return tuple(
            tuple(db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall())
            for table in (
                "source_references", "origin_links", "entitlements",
                "acquisition_ledger", "artifacts", "placements",
                "catalog_entries",
            )
        )


@pytest.mark.parametrize("account,owner", [
    ("buyer", "alice"),
    ("another-borrower", "alice"),
    ("other-borrower", "other-identity"),
])
def test_typed_source_prevents_any_first_legacy_canonical_mirror(
    storage, account, owner,
):
    _source(storage, account=account, owner=owner)
    before = _canonical(storage)
    storage.backup_put(_claim())
    assert storage.backup_get("kobo", "credential", "edition") is None
    # Replays do not create an old projection even for an alternate account.
    storage.backup_put(_claim(status="pending", revision="revision-two"))
    assert storage.backup_get("kobo", "credential", "edition") is None
    assert _canonical(storage) == before
    assert storage.entitlements() == []
    assert storage.acquisitions(provider_id="kobo") == []
    assert storage.placements() == []


def test_new_typed_source_prevents_legacy_replay_downgrading_old_right_and_ledger(storage):
    storage.backup_put(_claim())
    assert len(storage.entitlements(provider_id="kobo")) == 1
    assert len(storage.acquisitions(provider_id="kobo")) == 1
    _source(storage)
    before = _canonical(storage)
    storage.backup_put({
        **_claim(revision="revision-two", status="pending"),
        "title": "Historical projection refreshed",
    })
    raw = storage.backup_get("kobo", "credential", "edition")
    assert raw["revision_id"] == "revision-one"
    assert raw["status"] == "complete"
    assert raw["title"] == "Synthetic legacy title"
    # Canonical B and the old archive remain byte-for-byte unchanged.
    assert _canonical(storage) == before


def test_distinct_edition_keeps_legacy_compatibility_writer(storage):
    _source(storage, edition="other-edition")
    before = _canonical(storage)
    storage.backup_put(_claim())
    assert storage.backup_get("kobo", "credential", "edition") is not None
    assert len(storage.entitlements(provider_id="kobo")) == 1
    assert len(storage.acquisitions(provider_id="kobo")) == 1
    assert _canonical(storage) != before


def test_typed_source_cutover_is_key_scoped_and_does_not_freeze_legacy_sibling(storage):
    """Same credential, different edition still needs its legacy recovery."""
    storage.backup_put(_claim(item="older-edition", status="acquiring"))
    older = storage.backup_get("kobo", "credential", "older-edition")
    _source(storage, edition="edition", account="other-borrower")
    storage.backup_put(_claim(item="edition", status="complete"))
    storage.backup_put(_claim(item="older-edition", status="pending_acquisition"))
    assert storage.backup_get("kobo", "credential", "edition") is None
    refreshed = storage.backup_get("kobo", "credential", "older-edition")
    assert refreshed is not None
    assert refreshed["status"] == "pending_acquisition"
    assert older["status"] == "acquiring"


def test_typed_source_cutover_keeps_earlier_interrupted_legacy_receipt(storage):
    """The old checkpoint is retained as an archive, never reset or pruned."""
    storage.backup_put(_claim(status="acquiring"))
    before = storage.backup_get("kobo", "credential", "edition")
    _source(storage, account="other-borrower")
    storage.backup_put(_claim(status="skipped_archived", revision="revision-two"))
    assert storage.backup_get("kobo", "credential", "edition") == before
    assert storage.backup_prune_missing("kobo", "credential", set())["pruned"] == 0
    assert storage.backup_get("kobo", "credential", "edition") == before
