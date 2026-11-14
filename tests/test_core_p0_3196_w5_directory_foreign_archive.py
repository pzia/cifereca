"""#3196 W5: directory migration cannot claim any provider's foreign archive.

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


def _legacy(storage, path, owner, *, provider="kobo"):
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,title,status,updated_at,"
            "identity_id,local_path) VALUES(?,?,?,?,?,?,?,?)",
            (provider, "vault", "book", "Historic", "complete", 1, owner,
             str(path)),
        )


def _snapshot(storage):
    with storage._lock, storage._connect() as db:
        return tuple(
            tuple(db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
            for table in ("backup_items", "placements", "catalog_entries",
                          "acquisition_ledger", "metadata", "identities",
                          "source_references")
        )


@pytest.mark.parametrize("provider", ("kobo", "paris", "numilog", "synthetic-provider"))
@pytest.mark.parametrize("owner", (None, "bob", "alice-old"))
def test_directory_rename_refuses_foreign_or_unbound_archive(
    tmp_path, owner, provider,
):
    storage = Storage(tmp_path / "db.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        old = tmp_path / "alice-old" / "book.epub"
        _legacy(storage, old, owner, provider=provider)
        before = _snapshot(storage)
        with pytest.raises(
            ValueError, match="directory_migration_legacy_owner_requires_relink"
        ):
            storage.commit_directory_migration(_plan(tmp_path))
        assert _snapshot(storage) == before
        assert storage.metadata_get(JOURNAL_KEY) is None
    finally:
        storage.close()


@pytest.mark.parametrize("provider", ("kobo", "paris", "numilog", "synthetic-provider"))
def test_directory_rename_preserves_exact_owner_archive_locator(tmp_path, provider):
    storage = Storage(tmp_path / "db.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        old = tmp_path / "alice-old" / "book.epub"
        _legacy(storage, old, "alice", provider=provider)
        with storage._lock, storage._connect() as db:
            before = db.execute(
                "SELECT * FROM backup_items WHERE provider_id=?", (provider,)
            ).fetchone()
            db.execute(
                "CREATE TRIGGER deny_archive_relocation BEFORE UPDATE OF local_path "
                "ON backup_items BEGIN SELECT RAISE(ABORT,'archive_changed'); END"
            )
        storage.commit_directory_migration(_plan(tmp_path))
        # Historical locators are never rewritten to manufacture a B path.
        assert storage.backup_get(provider, "vault", "book")["local_path"] == str(old)
        with storage._lock, storage._connect() as db:
            assert db.execute(
                "SELECT * FROM backup_items WHERE provider_id=?", (provider,)
            ).fetchone() == before
        assert storage.identity_get("alice")["directory_name"] == "alice-new"
        assert storage.metadata_get(JOURNAL_KEY) is not None
    finally:
        storage.close()


@pytest.mark.parametrize("provider", ("kobo", "paris", "synthetic-provider"))
def test_unrelated_foreign_archive_outside_directory_is_untouched(tmp_path, provider):
    storage = Storage(tmp_path / "db.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        outside = tmp_path / "bob" / "book.epub"
        _legacy(storage, outside, "bob", provider=provider)
        storage.commit_directory_migration(_plan(tmp_path))
        assert storage.backup_get(provider, "vault", "book")["local_path"] == str(outside)
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


@pytest.mark.parametrize("provider", ("kobo", "paris", "numilog", "synthetic-provider"))
def test_typed_source_for_any_provider_blocks_archived_path_migration(tmp_path, provider):
    storage = Storage(tmp_path / "db.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        old = tmp_path / "alice-old" / "book.epub"
        _legacy(storage, old, "alice", provider=provider)
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO source_references("
                "source_reference_id,identity_id,provider_id,credential_id,"
                "account_id,branch_id,edition_id,right_id,reference_kind,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("typed-source", "alice", provider, "vault", "buyer",
                 "purchases", "book", "purchase", "purchase", 1, 1),
            )
        before = _snapshot(storage)
        with pytest.raises(
            ValueError, match="^directory_migration_canonical_source_requires_relink$",
        ):
            storage.commit_directory_migration(_plan(tmp_path))
        assert _snapshot(storage) == before
        assert storage.metadata_get(JOURNAL_KEY) is None
    finally:
        storage.close()
