"""#2565: a physical path shared by two supplier backups proves no owner.

Synthetic account, provider, edition and local disk data only.
"""
from __future__ import annotations

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _insert_backup(storage, *, provider, credential, item, local_path, identity="profile"):
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,title,status,updated_at,local_path,identity_id"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (provider, credential, item, "Synthetic book", "complete",
             1, str(local_path), identity),
        )


def test_shared_backup_path_is_not_attributed_to_sqlite_first_row(tmp_path):
    storage = Storage(tmp_path / "library.sqlite")
    book = tmp_path / "library" / "Book.pdf"
    book.parent.mkdir()
    book.write_bytes(b"%PDF-1.7\nSynthetic\n")
    _insert_backup(storage, provider="paris", credential="alice",
                   item="book-a", local_path=book)
    _insert_backup(storage, provider="paris", credential="bob",
                   item="book-b", local_path=book)

    assert storage.backup_find_by_local_path(str(book)) is None
    # A direct observation of the same file is still ambiguous even when
    # provider and physical bytes agree: borrower/edition are distinct.
    assert len(storage.backup_list("paris")) == 2

    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE backup_items SET local_path=? WHERE credential_id='bob'",
            (str(tmp_path / "other.pdf"),),
        )
    assert storage.backup_find_by_local_path(str(book))["credential_id"] == "alice"


def test_cross_provider_shared_backup_path_has_no_legacy_provenance(tmp_path):
    storage = Storage(tmp_path / "library.sqlite")
    root = tmp_path / "library"
    book = root / "profile" / "local" / "Book.pdf"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"%PDF-1.7\nSynthetic\n")
    storage.identity_put({
        "identity_id": "profile", "label": "Profile",
        "directory_name": "profile",
    })
    _insert_backup(storage, provider="paris", credential="one",
                   item="loan-one", local_path=book)
    _insert_backup(storage, provider="numilog", credential="two",
                   item="purchase-two", local_path=book)

    assert storage.backup_find_by_local_path(str(book)) is None
    result = LocalCatalog(storage, root).scan()

    assert result.cataloged == 1 and result.invalid == 0
    placement = storage.placements()[0]
    assert placement["identity_id"] == "profile"
    # Two conflicting historical receipts do not prove any provider; a
    # directory named 'local' cannot fill that gap.
    assert placement["provider_id"] is None
    assert placement["source_item_id"] is None
    entries = [
        row for row in storage.catalog_entries()
        if row["placement_id"] == placement["placement_id"]
    ]
    assert len(entries) == 1
    assert entries[0]["item_id"] is None
    assert storage.entitlements() == []
