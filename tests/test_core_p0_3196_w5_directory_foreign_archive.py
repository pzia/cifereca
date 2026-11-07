"""#3196 W5: identity directory migration cannot claim a Kobo archive of another owner.

Disposable SQLite and synthetic pathnames; no file copies or live provider.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage
from cifereca.identities.directory_migration import JOURNAL_KEY


def _plan(root):
    return {
        "root": str(root),
        "entries": [],
        "changes": [{
            "identity_id": "alice",
            "old": str(root / "alice-old"),
            "new": str(root / "alice-new"),
        }],
    }


def _legacy(storage, path, owner):
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,title,status,updated_at,"
            "identity_id,local_path) VALUES(?,?,?,?,?,?,?,?)",
            ("kobo", "vault", "book", "Historic", "complete", 1, owner,
             str(path)),
        )


def _snapshot(storage):
    with storage._lock, storage._connect() as db:
        return tuple(
            tuple(db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
            for table in ("backup_items", "placements", "catalog_entries",
                          "acquisition_ledger", "metadata", "identities")
        )


@pytest.mark.parametrize("owner", (None, "bob", "alice-old"))
def test_directory_rename_refuses_foreign_or_unbound_kobo_archive(
    tmp_path, owner,
):
    storage = Storage(tmp_path / "db.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        old = tmp_path / "alice-old" / "book.epub"
        _legacy(storage, old, owner)
        before = _snapshot(storage)
        with pytest.raises(
            ValueError, match="directory_migration_legacy_owner_requires_relink"
        ):
            storage.commit_directory_migration(_plan(tmp_path))
        assert _snapshot(storage) == before
        assert storage.metadata_get(JOURNAL_KEY) is None
    finally:
        storage.close()


def test_directory_rename_updates_only_exact_owner_legacy_history(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        old = tmp_path / "alice-old" / "book.epub"
        _legacy(storage, old, "alice")
        storage.commit_directory_migration(_plan(tmp_path))
        assert storage.backup_get("kobo", "vault", "book")["local_path"] == (
            str(tmp_path / "alice-new" / "book.epub")
        )
        assert storage.identity_get("alice")["directory_name"] == "alice-new"
        assert storage.metadata_get(JOURNAL_KEY) is not None
    finally:
        storage.close()


def test_unrelated_foreign_archive_outside_directory_is_untouched(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        outside = tmp_path / "bob" / "book.epub"
        _legacy(storage, outside, "bob")
        storage.commit_directory_migration(_plan(tmp_path))
        assert storage.backup_get("kobo", "vault", "book")["local_path"] == str(outside)
    finally:
        storage.close()


def test_legacy_path_parent_traversal_cannot_escape_owner_check(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        claimed = tmp_path / "alice-old" / ".." / "bob" / "book.epub"
        _legacy(storage, claimed, "alice")
        before = _snapshot(storage)
        with pytest.raises(
            ValueError, match="directory_migration_legacy_owner_requires_relink"
        ):
            storage.commit_directory_migration(_plan(tmp_path))
        assert _snapshot(storage) == before
    finally:
        storage.close()
