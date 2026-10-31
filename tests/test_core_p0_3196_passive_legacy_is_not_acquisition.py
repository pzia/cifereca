"""Core P0 #3196: legacy catalog receipts are not a second acquisition writer."""
from __future__ import annotations

import hashlib

import pytest

from cifereca.storage import Storage


def _row(path, status):
    data = b"offline historical fixture"
    return {
        "provider_id": "kobo", "credential_id": "vault-a",
        "identity_id": "alice", "account_id": "buyer-a",
        "item_id": "edition-1", "entitlement_type": "owned",
        "revision_id": "legacy-revision", "title": "Legacy title",
        "status": status, "local_path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data), "format": "epub",
    }


def _attest(store):
    return store.entitlement_put({
        "provider_id": "kobo", "credential_id": "vault-a",
        "identity_id": "alice", "account_id": "buyer-a",
        "item_id": "edition-1", "entitlement_type": "owned",
        "revision_id": "canonical-provider", "state": "active",
        "metadata": {"source": "attested-provider-inventory"},
    })


@pytest.mark.parametrize("legacy_status", [
    "pending", "queued", "pending_acquisition", "unknown",
    "reconcile_required", "reconcile_conflict", "skipped_archived",
])
def test_passive_legacy_replay_does_not_create_canonical_content_or_receipt(
    tmp_path, legacy_status,
):
    store = Storage(tmp_path / "state.sqlite3")
    try:
        path = tmp_path / "legacy.epub"
        path.write_bytes(b"offline historical fixture")
        _attest(store)
        rights = store.entitlements()
        with store._lock, store._connect() as db:
            before = {
                table: db.execute("SELECT * FROM " + table).fetchall()
                for table in ("entitlements", "acquisition_ledger", "artifacts",
                              "placements", "catalog_entries", "source_references",
                              "origin_links")
            }
        for attempt in range(2):
            store.backup_put({
                **_row(path, legacy_status),
                "title": "Compatibility only " + str(attempt),
            })
            assert store.entitlements() == rights
            with store._lock, store._connect() as db:
                for table, expected in before.items():
                    assert db.execute("SELECT * FROM " + table).fetchall() == expected
        historical = store.backup_get("kobo", "vault-a", "edition-1")
        assert historical["status"] == legacy_status
        assert historical["title"] == "Compatibility only 1"
        assert path.read_bytes() == b"offline historical fixture"
    finally:
        store.close()


def test_passive_legacy_does_not_downgrade_an_independent_canonical_receipt(
    tmp_path,
):
    store = Storage(tmp_path / "state.sqlite3")
    try:
        path = tmp_path / "old.epub"
        row = _row(path, "pending")
        right = _attest(store)
        store.acquisition_put({
            "acquisition_id": store._acquisition_id_for(row),
            "entitlement_id": right,
            "provider_id": "kobo", "credential_id": "vault-a",
            "identity_id": "alice", "account_id": "buyer-a",
            "item_id": "edition-1", "status": "complete",
            "metadata": {"source": "Core-v2"},
        })
        receipts = store.acquisitions(provider_id="kobo")
        store.backup_put(row)
        assert store.acquisitions(provider_id="kobo") == receipts
        assert store.backup_get("kobo", "vault-a", "edition-1")["status"] == "pending"
        assert store.artifacts() == []
    finally:
        store.close()


def test_active_legacy_delivery_remains_compatible_while_writer_exists(tmp_path):
    store = Storage(tmp_path / "state.sqlite3")
    try:
        path = tmp_path / "delivered.epub"
        _attest(store)
        row = _row(path, "complete")
        store.backup_put(row)
        assert store.acquisitions(provider_id="kobo")[0]["status"] == "complete"
        assert store.artifact_get("sha256:" + row["sha256"]) is not None
        assert store.backup_get("kobo", "vault-a", "edition-1")["status"] == "complete"
        # This is still a legacy placeholder, not an OriginLink attesting a
        # provider right or proof of physical publication.
        assert store.placements()[0]["state"] == "unverified"
        with store._lock, store._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
    finally:
        store.close()
