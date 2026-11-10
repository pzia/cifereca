"""P0 #3196: newly written 0.7 Kobo product keys must be SourceKey-qualifiable.

The old SQLite table may contain malformed historical keys: neither this
test nor the production guard deletes/rewrites those legacy rows. The writer
must not create any additional unqualifiable projections or phantom rights.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _claim(item: object):
    return {
        "provider_id": "kobo", "credential_id": "vault",
        "account_id": "buyer", "identity_id": "alice",
        "item_id": item, "revision_id": "rev-1", "title": "Synthetic",
        "entitlement_type": "owned", "status": "complete",
    }


def _snapshot(storage):
    with storage._lock, storage._connect() as db:
        return tuple(
            tuple(db.execute("SELECT * FROM " + name + " ORDER BY 1").fetchall())
            for name in (
                "backup_items", "acquisition_ledger", "entitlements",
                "artifacts", "placements", "catalog_entries",
                "source_references", "origin_links",
            )
        )


@pytest.mark.parametrize("unqualified", [
    None, "", "  ", " book ", "book ", "book\x00other",
    "book\nother", "book\rother", "x" * 1025, 1234,
])
def test_kobo_writer_refuses_new_unqualified_keys_atomically(tmp_path, unqualified):
    storage = Storage(tmp_path / "legacy.sqlite")
    try:
        storage.backup_put(_claim("book"))
        before = _snapshot(storage)
        with pytest.raises(ValueError, match="backup_legacy_item_id_invalid"):
            storage.backup_put(_claim(unqualified))
        assert _snapshot(storage) == before
        assert storage.backup_get("kobo", "vault", "book") is not None
    finally:
        storage.close()


def test_historical_corrupt_sqlite_key_is_not_deleted_by_write_admission(tmp_path):
    storage = Storage(tmp_path / "legacy.sqlite")
    try:
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,title,status,updated_at"
                ") VALUES(?,?,?,?,?,?)",
                ("kobo", "vault", "", "Historical", "complete", 1),
            )
        before = _snapshot(storage)
        with pytest.raises(ValueError, match="backup_legacy_item_id_invalid"):
            storage.backup_put(_claim(""))
        assert _snapshot(storage) == before
        assert storage.backup_get("kobo", "vault", "") is not None
    finally:
        storage.close()


def test_valid_kobo_product_id_remains_writable_and_replayable(tmp_path):
    storage = Storage(tmp_path / "legacy.sqlite")
    try:
        storage.backup_put(_claim("book"))
        first = storage.backup_get("kobo", "vault", "book")
        assert first is not None
        storage.backup_put({**_claim("book"), "title": "Updated"})
        assert storage.backup_get("kobo", "vault", "book")["title"] == "Updated"
        assert storage.acquisitions(provider_id="kobo") == []
    finally:
        storage.close()


def test_kobo_prune_preserves_historical_padded_id_not_in_manifest(tmp_path):
    # Legacy SQLite accepted padded Kobo product IDs. The typed manifest
    # cannot establish that such an opaque historical source was removed.
    storage = Storage(tmp_path / "legacy.sqlite")
    try:
        storage.backup_put(_claim("stale"))
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,title,status,updated_at"
                ") VALUES(?,?,?,?,?,?)",
                ("kobo", "vault", " book ", "Historical", "complete", 1),
            )
        before_rights = storage.entitlements(provider_id="kobo")
        before_ledger = storage.acquisitions(provider_id="kobo")
        before_padded = storage.backup_get("kobo", "vault", " book ")
        assert storage.backup_prune_missing("kobo", "vault", {"another"}) == {
            "pruned": 0, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert storage.backup_get("kobo", "vault", "stale") is not None
        assert storage.backup_get("kobo", "vault", " book ") == before_padded
        assert storage.entitlements(provider_id="kobo") == before_rights
        assert storage.acquisitions(provider_id="kobo") == before_ledger
    finally:
        storage.close()
