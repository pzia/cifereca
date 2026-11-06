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
            "status": status,
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
