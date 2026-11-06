"""#3196 W1: archived/reconciliation-only Kobo receipts are never Core writers.

Synthetic SQLite fixtures. The legacy 0.7 row remains available for
explicit review; neither its status nor its SHA is provider-attested evidence.
"""
from __future__ import annotations

import hashlib

import pytest

from cifereca.storage import Storage


@pytest.mark.parametrize("status", ("skipped_archived",))
def test_passive_kobo_checkpoint_does_not_mint_canonical_state(tmp_path, status):
    storage = Storage(tmp_path / "state.sqlite3")
    data = b"synthetic historical content"
    row = {
        "provider_id": "kobo", "credential_id": "vault",
        "identity_id": "alice", "account_id": "account-a",
        "item_id": "edition", "revision_id": "old-revision",
        "title": "Archived", "status": status, "format": "epub",
        "local_path": str(tmp_path / "historical.epub"),
        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
    }
    try:
        with storage._lock, storage._connect() as db:
            tables = ("entitlements", "acquisition_ledger", "artifacts",
                      "placements", "catalog_entries", "source_references",
                      "origin_links")
            before = {
                table: db.execute(f"SELECT * FROM {table}").fetchall()
                for table in tables
            }
        for _ in range(2):
            storage.backup_put(row)
            assert storage.backup_get("kobo", "vault", "edition")["status"] == status
            with storage._lock, storage._connect() as db:
                for table in tables:
                    assert db.execute(f"SELECT * FROM {table}").fetchall() == before[table]
    finally:
        storage.close()


@pytest.mark.parametrize("status", ("skipped_archived",))
def test_passive_kobo_checkpoint_cannot_rebind_existing_canonical_owner(
    tmp_path, status,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        right = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "entitlement_type": "owned",
            "revision_id": "provider-current", "state": "active",
        }
        storage.entitlement_put(right)
        before = storage.entitlements()
        row = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "bob", "account_id": "account-a",
            "item_id": "edition", "entitlement_type": "owned",
            "revision_id": "old", "title": "Old",
            "status": status,
        }
        with pytest.raises(ValueError, match="backup_entitlement_owner_conflict"):
            storage.backup_put(row)
        assert storage.backup_get("kobo", "vault", "edition") is None
        assert storage.entitlements() == before
    finally:
        storage.close()


@pytest.mark.parametrize("status", ("reconcile_required", "reconcile_conflict"))
def test_w4_reconciliation_mirror_remains_available_until_migrated(
    tmp_path, status,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "title": "Review",
            "status": status, "sha256": "a" * 64, "size": 42,
        })
        assert storage.backup_get("kobo", "vault", "edition")["status"] == status
        assert len(storage.acquisitions(provider_id="kobo")) == 1
    finally:
        storage.close()


def test_other_provider_retains_historical_projection(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.backup_put({
            "provider_id": "external", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "title": "External",
            "status": "reconcile_required",
        })
        assert storage.backup_get("external", "vault", "edition") is not None
        assert storage.entitlements(provider_id="external")
    finally:
        storage.close()



@pytest.mark.parametrize("sha,size", [
    (None, None), ("a" * 64, None), (None, 12),
    ("bad-sha", 12), ("a" * 63, 12), ("a" * 64, 0),
    ("z" * 64, 12),
])
@pytest.mark.parametrize("status", ("reconcile_required", "reconcile_conflict"))
def test_unprovable_kobo_reconciliation_never_creates_positive_canonical_state(
    tmp_path, sha, size, status,
):
    """W4 cannot confirm missing SHA or size; archive the checkpoint only."""
    storage = Storage(tmp_path / "state.sqlite3")
    row = {
        "provider_id": "kobo", "credential_id": "vault",
        "identity_id": "alice", "account_id": "account-a",
        "item_id": "edition", "revision_id": "revision-a",
        "title": "Unresolved local candidates", "status": status,
        "sha256": sha, "size": size,
        "local_path": str(tmp_path / "ambiguous.epub"),
    }
    try:
        with storage._lock, storage._connect() as db:
            tables = (
                "entitlements", "acquisition_ledger", "artifacts",
                "placements", "catalog_entries", "source_references", "origin_links",
            )
            before = {
                table: db.execute("SELECT * FROM " + table).fetchall()
                for table in tables
            }
        for _ in range(2):
            storage.backup_put(row)
            assert storage.backup_get("kobo", "vault", "edition")["status"] == status
            with storage._lock, storage._connect() as db:
                assert all(
                    db.execute("SELECT * FROM " + table).fetchall() == before[table]
                    for table in tables
                )
    finally:
        storage.close()


def test_unprovable_conflict_retains_owner_collision_veto(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "entitlement_type": "owned",
            "state": "active",
        })
        with pytest.raises(ValueError, match="backup_entitlement_owner_conflict"):
            storage.backup_put({
                "provider_id": "kobo", "credential_id": "vault",
                "identity_id": "bob", "account_id": "account-a",
                "item_id": "edition", "entitlement_type": "owned",
                "title": "Unproven", "status": "reconcile_conflict",
            })
        assert storage.backup_get("kobo", "vault", "edition") is None
    finally:
        storage.close()


def test_unprovable_required_patch_preserves_previous_typed_canonical_evidence(tmp_path):
    """A 0.7 error must not downgrade independent Core rows on a retry."""
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        right = storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "vault",
            "account_id": "account-a", "identity_id": "alice",
            "item_id": "edition", "entitlement_type": "owned",
            "revision_id": "provider-v2", "state": "active",
        })
        storage.acquisition_put({
            "acquisition_id": "independent-123",
            "entitlement_id": right, "provider_id": "kobo",
            "credential_id": "vault", "account_id": "account-a",
            "identity_id": "alice", "item_id": "edition",
            "status": "complete", "metadata": {"source": "provider-v2"},
        })
        before = storage.entitlements(), storage.acquisitions()
        row = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "revision_id": "historical-v1",
            "status": "reconcile_required", "sha256": None,
            "size": None, "local_path": str(tmp_path / "lost.epub"),
        }
        storage.backup_put(row)
        storage.backup_put({**row, "title": "Retried unproven"})
        assert storage.entitlements() == before[0]
        assert storage.acquisitions() == before[1]
        assert storage.backup_get("kobo", "vault", "edition")["title"] == "Retried unproven"
    finally:
        storage.close()
