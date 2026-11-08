"""#3196 W1: replay of an already-complete Kobo archive is metadata-only.

A 0.7 complete SHA/path/revision is no independent supplier A/B proof.
It can remain in backup_items for historical display/rollback, but must not
mint/refresh canonical entitlement, acquisition, artifact or placement on a
second passive metadata refresh. Use real SQLite v14.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


_CANONICAL = (
    "entitlements", "acquisition_ledger", "artifacts", "placements",
    "catalog_entries", "source_references", "origin_links",
)


def _snapshot(storage):
    with storage._lock, storage._connect() as db:
        return {
            table: tuple(db.execute(
                "SELECT * FROM " + table + " ORDER BY 1"
            ).fetchall())
            for table in _CANONICAL
        }


def _row(*, status="complete", title="Old title", cover=None, owner="alice"):
    return {
        "provider_id": "kobo", "credential_id": "vault",
        "identity_id": owner, "account_id": "buyer",
        "item_id": "edition", "revision_id": "rev-1",
        "entitlement_type": "owned", "status": status,
        "title": title, "format": "epub",
        "sha256": "a" * 64, "size": 128,
        "local_path": "/historical/alice/kobo/edition.epub",
        "cover_url": cover,
    }


@pytest.mark.parametrize("previous_status", ("complete", "ok"))
def test_existing_complete_kobo_archive_metadata_refresh_never_mints_rights(
    tmp_path, previous_status,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        conn = storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "enabled": True,
        })
        storage.provider_account_put({
            "connection_id": conn, "identity_id": "alice",
            "provider_id": "kobo", "credential_id": "vault",
            "account_id": "buyer", "is_primary": True,
        })
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,identity_id,account_id,"
                "revision_id,title,format,status,local_path,sha256,size,"
                "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("kobo", "vault", "edition", "alice", "buyer", "rev-1",
                 "Historic title", "epub", previous_status,
                 "/historical/alice/kobo/edition.epub", "a" * 64, 128, 10),
            )
        assert not any(_snapshot(storage).values())
        updated = _row(title="New title", cover="https://example.invalid/cover.jpg")
        storage.backup_put(updated)
        assert not any(_snapshot(storage).values())
        archived = storage.backup_get("kobo", "vault", "edition")
        assert archived["status"] == "complete"
        assert archived["title"] == updated["title"]
        assert archived["cover_url"] == updated["cover_url"]
        assert archived["revision_id"] == "rev-1"
        assert archived["sha256"] == "a" * 64
        storage.backup_put(_row(title="Another cover", cover="https://example.invalid/next"))
        assert not any(_snapshot(storage).values())
    finally:
        storage.close()


def test_first_legacy_delivery_remains_compatible_but_replay_cannot_reproject(
    tmp_path,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        # W2 first receipt remains compatible until A->B migration; no
        # accidental global shutdown of the old executor in this W1 slice.
        storage.backup_put(_row(status="acquiring"))
        storage.backup_put(_row(status="complete"))
        positive = _snapshot(storage)
        assert positive["acquisition_ledger"]
        storage.backup_put(_row(status="complete", title="Cosmetic refresh"))
        after = _snapshot(storage)
        assert after == positive
        assert storage.backup_get("kobo", "vault", "edition")["title"] == "Cosmetic refresh"
    finally:
        storage.close()


def test_foreign_borrower_cannot_relabel_existing_complete_archive(
    tmp_path,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.backup_put(_row(status="complete"))
        canonical = _snapshot(storage)
        historical = storage.backup_get("kobo", "vault", "edition")
        with pytest.raises(ValueError, match="backup_projection_owner_conflict"):
            storage.backup_put({
                **_row(title="Rebound"),
                "account_id": "foreign-borrower",
            })
        assert storage.backup_get("kobo", "vault", "edition") == historical
        assert _snapshot(storage) == canonical
    finally:
        storage.close()
