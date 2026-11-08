"""#3196 W1: a first unfinished Kobo 0.7 checkpoint cannot mint Core A/B.

Real v14 SQLite without a provider, remote GET, or user library files.
Complete first delivery remains temporarily compatible until W2 is migrated.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _row(*, status="pending", owner="alice", borrower="buyer",
         revision="v1", sha=None, path=None):
    return {
        "provider_id": "kobo", "credential_id": "vault",
        "identity_id": owner, "account_id": borrower,
        "item_id": "edition", "revision_id": revision,
        "title": "Historic edition", "format": "epub",
        "entitlement_type": "owned", "status": status,
        "sha256": sha, "size": 50 if sha else None,
        "local_path": path,
    }


def _b(storage):
    with storage._lock, storage._connect() as db:
        return {
            name: tuple(db.execute(
                f"SELECT * FROM {name} ORDER BY 1"
            ).fetchall())
            for name in (
                "entitlements", "acquisition_ledger",
                "artifacts", "placements", "catalog_entries",
                "source_references", "origin_links",
            )
        }


@pytest.mark.parametrize("status", (
    "pending", "queued", "acquiring", "error", "failed",
    "attention_required", "pending_acquisition",
    "reconcile_required", "reconcile_conflict", "skipped_archived", "ok",
))
def test_first_unfinished_kobo_row_is_compatibility_archive_only(
    tmp_path, status,
):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        row = _row(
            status=status, revision="unverified",
            sha="a" * 64, path="/historical/alice/kobo/edition.epub",
        )
        storage.backup_put(row)
        assert storage.backup_get("kobo", "vault", "edition")["status"] == status
        assert not any(_b(storage).values())
        storage.backup_put({**row, "title": "Replayed unfinished"})
        assert not any(_b(storage).values())
        assert storage.backup_get("kobo", "vault", "edition")["title"] == "Replayed unfinished"
    finally:
        storage.close()


def test_legacy_w2_first_complete_keeps_one_compatibility_delivery_until_cutover(
    tmp_path,
):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        for status in ("pending", "acquiring", "error"):
            storage.backup_put(_row(status=status))
            assert not any(_b(storage).values())
        delivered = _row(
            status="complete", sha="b" * 64,
            path="/historical/alice/kobo/edition.epub",
        )
        storage.backup_put(delivered)
        after = _b(storage)
        assert len(after["entitlements"]) == 1
        assert len(after["acquisition_ledger"]) == 1
        assert after["acquisition_ledger"][0]
        assert not after["source_references"]
        assert not after["origin_links"]
        # A second 0.7 retry cannot demote/duplicate any canonical table.
        for status in ("pending", "acquiring", "error", "complete"):
            storage.backup_put({**delivered, "status": status, "revision_id": "changed"})
            assert _b(storage) == after
        assert storage.backup_get("kobo", "vault", "edition")["status"] == "complete"
    finally:
        storage.close()


def test_initial_pending_owner_collision_keeps_legacy_archive_atomic(
    tmp_path,
):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        storage.backup_put(_row(status="pending"))
        first = storage.backup_get("kobo", "vault", "edition")
        before = _b(storage)
        with pytest.raises(ValueError, match="backup_projection_owner_conflict"):
            storage.backup_put(_row(status="acquiring", owner="other"))
        assert storage.backup_get("kobo", "vault", "edition") == first
        assert _b(storage) == before
    finally:
        storage.close()
