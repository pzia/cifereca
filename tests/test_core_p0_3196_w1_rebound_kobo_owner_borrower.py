"""P0 #3196 W1: stale legacy receipts cannot mint a right for another
Kobo identity or borrower when exact current SQLite bindings exist.

No provider/network contact and no attestation of historical SHA/path.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


CANONICAL_TABLES = (
    "entitlements", "acquisition_ledger", "artifacts", "placements",
    "catalog_entries", "source_references", "origin_links",
)


def _canonical_snapshot(storage):
    with storage._lock, storage._connect() as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in CANONICAL_TABLES
        }


def _receipt(*, identity="alice", account="account-a", credential="vault",
             edition="edition", status="complete"):
    return {
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": identity, "account_id": account,
        "item_id": edition, "revision_id": "historic-r1",
        "status": status, "title": "Legacy item", "size": 42,
        "sha256": "a" * 64, "format": "epub",
        "local_path": "/nonexistent/kobo-legacy-only.epub",
    }


@pytest.mark.parametrize("status", (
    "complete", "pending", "acquiring", "reconcile_required",
))
def test_enabled_connection_foreign_owner_stays_archive_only(tmp_path, status):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "kobo",
            "credential_id": "vault", "enabled": True,
        })
        before = _canonical_snapshot(storage)
        for _ in range(2):
            storage.backup_put(_receipt(status=status))
            row = storage.backup_get("kobo", "vault", "edition")
            assert row is not None and row["status"] == status
            assert row["identity_id"] == "alice"
            assert _canonical_snapshot(storage) == before
    finally:
        storage.close()


def test_rebound_connection_cannot_refresh_existing_canonical_backup_projection(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "enabled": True,
        })
        storage.backup_put(_receipt(status="pending"))
        assert storage.entitlements(provider_id="kobo")
        before = _canonical_snapshot(storage)
        storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "kobo",
            "credential_id": "vault", "enabled": True,
        })
        storage.backup_put(_receipt(status="complete"))
        assert storage.backup_get("kobo", "vault", "edition")["status"] == "complete"
        assert _canonical_snapshot(storage) == before
    finally:
        storage.close()


def test_present_borrower_mapping_vetoes_foreign_account_but_allows_exact(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        cid = storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "enabled": True,
        })
        storage.provider_account_put({
            "connection_id": cid, "identity_id": "alice",
            "provider_id": "kobo", "credential_id": "vault",
            "account_id": "account-a",
        })
        before = _canonical_snapshot(storage)
        storage.backup_put(_receipt(account="account-b"))
        assert storage.backup_get("kobo", "vault", "edition")
        assert _canonical_snapshot(storage) == before
        storage.backup_put(_receipt(account="account-a", edition="edition-ok"))
        rights = storage.entitlements(provider_id="kobo")
        assert len(rights) == 1
        assert rights[0]["account_id"] == "account-a"
        assert rights[0]["item_id"] == "edition-ok"
    finally:
        storage.close()


def test_unregistered_or_unmapped_legacy_scope_remains_compatible(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.backup_put(_receipt(edition="no-connection"))
        storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "bound", "enabled": True,
        })
        storage.backup_put(_receipt(credential="bound", edition="no-account-mapping"))
        rights = storage.entitlements(provider_id="kobo")
        assert {row["item_id"] for row in rights} == {
            "no-connection", "no-account-mapping",
        }
    finally:
        storage.close()
