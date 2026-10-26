"""#2847: invalid physical presence must atomically invalidate catalogue projection.

Synthetic disk/SQLite only; no account, DRM or network operations.
"""
from __future__ import annotations

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _case(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    file = root / "Book.pdf"
    file.write_bytes(b"%PDF-1.7\nSynthetic\n")
    storage = Storage(tmp_path / "library.sqlite")
    catalog = LocalCatalog(storage, root)
    first = catalog.scan()
    assert first.cataloged == 1
    placement = storage.placements()[0]
    assert placement["state"] == "present"
    entries = [e for e in storage.catalog_entries()
               if e["placement_id"] == placement["placement_id"]]
    assert len(entries) == 1 and entries[0]["local_state"] == "local"
    return storage, catalog, file, placement["placement_id"]


def test_invalid_pdf_scan_downgrades_placement_and_catalogue_together(tmp_path):
    storage, catalog, file, placement_id = _case(tmp_path)
    original = file.read_bytes()
    file.write_bytes(b"NOT_PDF_BYTES")

    result = catalog.scan()

    assert result.invalid == 1
    assert storage.placement_get(placement_id)["state"] == "invalid"
    entries = [e for e in storage.catalog_entries()
               if e["placement_id"] == placement_id]
    assert len(entries) == 1
    assert entries[0]["local_state"] == "invalid"
    assert entries[0]["metadata"]["path"] == str(file)
    assert file.read_bytes() == b"NOT_PDF_BYTES"

    # An independently observed valid file can become locally available
    # again, but the scan never manufactures any supplier right.
    file.write_bytes(original)
    restored = catalog.scan()
    assert restored.cataloged == 1
    assert storage.placement_get(placement_id)["state"] == "present"
    assert any(e["local_state"] == "local" and e["placement_id"] == placement_id
               for e in storage.catalog_entries())
    assert storage.entitlements() == []


def test_invalid_placement_updates_all_linked_catalogues_in_one_commit(tmp_path):
    storage, _, file, placement_id = _case(tmp_path)
    storage.catalog_entry_put({
        "catalog_entry_id": "synthetic-alternate-entry",
        "placement_id": placement_id, "artifact_id": None,
        "title": "Another projection", "local_state": "local",
        "metadata": {"path": str(file)},
    })

    storage.placement_put({
        "placement_id": placement_id, "path": str(file),
        "state": "invalid", "metadata": {"invalid_reason": "unreadable_file"},
    })

    entries = [e for e in storage.catalog_entries()
               if e["placement_id"] == placement_id]
    assert len(entries) == 2
    assert all(e["local_state"] == "invalid" for e in entries)
    assert all(e["metadata"]["path"] == str(file) for e in entries)


def test_path_conflict_uses_stored_placement_id_not_caller_supplied_id(tmp_path):
    storage, _, file, placement_id = _case(tmp_path)

    storage.placement_put({
        "placement_id": "incorrect-new-id",
        "path": str(file), "state": "invalid",
        "metadata": {"invalid_reason": "extension_mismatch"},
    })

    assert storage.placement_get(placement_id)["state"] == "invalid"
    assert storage.placement_get("incorrect-new-id") is None
    assert all(e["local_state"] == "invalid" for e in storage.catalog_entries()
               if e["placement_id"] == placement_id)
