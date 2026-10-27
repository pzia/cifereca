"""#2565: legacy path provenance never crosses local profile boundaries.

Provider, profile and book values are synthetic and never contact the network.
"""
from __future__ import annotations

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


@pytest.mark.parametrize(("backup_owner", "expected_provider", "expected_item"), [
    ("profile-a", None, None),
    (None, None, None),
    ("profile-b", "numilog", "edition-b"),
])
def test_only_exact_identity_backup_can_supply_legacy_local_provenance(
    tmp_path, backup_owner, expected_provider, expected_item,
):
    root = tmp_path / "library"
    book = root / "profile-b" / "local" / "Book.pdf"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"%PDF-1.7\nsynthetic\n")
    storage = Storage(tmp_path / "library.sqlite")
    for identity in ("profile-a", "profile-b"):
        storage.identity_put({
            "identity_id": identity, "directory_name": identity,
            "label": identity,
        })
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,status,updated_at,local_path,identity_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("numilog", "credential-a", "edition-b",
             "Synthetic book", "complete", 1, str(book), backup_owner),
        )

    catalog = LocalCatalog(storage, root)
    first = catalog.scan()
    second = catalog.scan()

    assert first.cataloged == second.cataloged == 1
    assert first.invalid == second.invalid == 0
    placements = storage.placements()
    assert len(placements) == 1
    assert placements[0]["state"] == "present"
    assert placements[0]["identity_id"] == "profile-b"
    assert placements[0]["provider_id"] == expected_provider
    assert placements[0]["source_item_id"] == expected_item
    entries = [
        row for row in storage.catalog_entries()
        if row["placement_id"] == placements[0]["placement_id"]
    ]
    assert entries
    assert all(row["identity_id"] == "profile-b" for row in entries)
    assert all(row["provider_id"] == expected_provider for row in entries)
    assert all(row["item_id"] == expected_item for row in entries)
    assert storage.entitlements() == []
