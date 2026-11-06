"""#3196 W3: a complete manifest is not permission to erase an old file locator.

Only synthetic SQLite and optional disposable bytes; never contact Kobo.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _claim(item: str, *, path=None, credential="vault"):
    return {
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": "alice", "account_id": "buyer",
        "item_id": item, "title": "Historical synthetic book",
        "status": "complete", "entitlement_type": "owned",
        "local_path": str(path) if path is not None else None,
    }


def _immutable_rows(storage):
    with storage._lock, storage._connect() as sql:
        return tuple(
            tuple(sql.execute("SELECT * FROM " + table + " ORDER BY 1"))
            for table in (
                "entitlements", "acquisition_ledger", "artifacts",
                "placements", "catalog_entries", "source_references",
                "origin_links",
            )
        )


@pytest.mark.parametrize("path_kind", [
    "existing_file", "temporarily_missing", "unresolved_relative",
    "symlink_outside",
])
def test_absent_manifest_cannot_prune_only_historical_kobo_file_locator(
    tmp_path, path_kind,
):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        root = tmp_path / "library"
        root.mkdir()
        original = root / "historical.epub"
        if path_kind == "existing_file":
            original.write_bytes(b"synthetic local book")
        elif path_kind == "symlink_outside":
            outside = tmp_path / "outside.epub"
            outside.write_bytes(b"unrelated book")
            original.symlink_to(outside)
        elif path_kind == "unresolved_relative":
            original = "relative/unqualified.epub"
        # Missing disk or symlink must never be used as deletion evidence.
        storage.backup_put(_claim("book", path=original))
        storage.backup_put(_claim("stale-no-locator"))
        other = storage.backup_get("kobo", "vault", "book")
        before = _immutable_rows(storage)
        outcome = storage.backup_prune_missing("kobo", "vault", set())
        assert outcome == {
            "pruned": 0, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert storage.backup_get("kobo", "vault", "book") == other
        assert storage.backup_get("kobo", "vault", "stale-no-locator") is not None
        assert _immutable_rows(storage) == before
        if path_kind == "existing_file":
            assert original.read_bytes() == b"synthetic local book"
        if path_kind == "symlink_outside":
            assert original.is_symlink()
            assert outside.read_bytes() == b"unrelated book"
    finally:
        storage.close()


def test_same_edition_in_other_credential_is_not_a_prune_permission(tmp_path):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        historical = tmp_path / "offline.epub"
        storage.backup_put(_claim("book", path=historical, credential="one"))
        storage.backup_put(_claim("book", credential="two"))
        preserved = storage.backup_get("kobo", "one", "book")
        assert storage.backup_prune_missing("kobo", "two", set())["pruned"] == 0
        assert storage.backup_prune_missing("kobo", "one", set())["pruned"] == 0
        assert storage.backup_get("kobo", "one", "book") == preserved
        assert storage.backup_get("kobo", "two", "book") is not None
    finally:
        storage.close()


def test_blank_locator_is_not_persisted_file_evidence(tmp_path):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        # Only a whitespace path claim is unusable even as a locator.
        storage.backup_put(_claim("blank", path="   "))
        assert storage.backup_prune_missing("kobo", "vault", set()) == {
            "pruned": 0, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert storage.backup_get("kobo", "vault", "blank") is not None
    finally:
        storage.close()
