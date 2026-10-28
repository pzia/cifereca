"""#3196: a v0.7 backup path must never become supplier/edition authority.

All accounts, identities and PDF bytes are synthetic. No provider/DRM I/O.
"""
from __future__ import annotations

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _case(tmp_path):
    root = tmp_path / "library"
    book = root / "profile-a" / "manual" / "Book.pdf"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"%PDF-1.7\nSynthetic catalog fixture\n")
    storage = Storage(tmp_path / "state.sqlite")
    storage.identity_put({
        "identity_id": "profile-a",
        "directory_name": "profile-a",
        "label": "A",
    })
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,title,author,status,"
            "updated_at,local_path,identity_id"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            ("kobo", "credential-a", "legacy-edition",
             "Legacy purchase title", "Legacy author", "complete",
             1, str(book), "profile-a"),
        )
    return storage, LocalCatalog(storage, root), book


@pytest.mark.parametrize("mode", ("scan", "targeted"))
def test_unique_matching_backup_never_supplies_new_placement_origin(tmp_path, mode):
    storage, catalog, book = _case(tmp_path)
    if mode == "scan":
        result = catalog.scan()
        assert result.cataloged == 1 and result.invalid == 0
    else:
        assert catalog.index_path(book)["status"] == "indexed"

    placement = storage.placement_for_path(book)
    assert placement is not None
    assert placement["identity_id"] == "profile-a"
    assert placement["provider_id"] is None
    assert placement["source_item_id"] is None
    assert placement["state"] == "present"
    entries = [row for row in storage.catalog_entries()
               if row["placement_id"] == placement["placement_id"]]
    assert len(entries) == 1
    assert entries[0]["provider_id"] is None
    assert entries[0]["item_id"] is None
    # History is retained for the forward migration; it is not a new right.
    assert len(storage.backup_list("kobo")) == 1
    assert storage.entitlements() == []


@pytest.mark.parametrize("mode", ("scan", "targeted"))
def test_legacy_backup_cannot_override_independent_existing_placement(tmp_path, mode):
    storage, catalog, book = _case(tmp_path)
    placement_id = storage.placement_put({
        "path": str(book), "artifact_id": None, "identity_id": "profile-a",
        "provider_id": "numilog", "source_item_id": "canonical-edition",
        "state": "present", "metadata": {"synthetic": True},
    })
    if mode == "scan":
        result = catalog.scan()
        assert result.cataloged == 1 and result.invalid == 0
    else:
        assert catalog.index_path(book)["status"] == "indexed"

    placement = storage.placement_get(placement_id)
    assert placement["identity_id"] == "profile-a"
    assert placement["provider_id"] == "numilog"
    assert placement["source_item_id"] == "canonical-edition"
    assert placement["state"] == "present"
    assert len(storage.backup_list("kobo")) == 1
    assert storage.entitlements() == []
