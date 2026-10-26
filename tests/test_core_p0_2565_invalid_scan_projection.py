"""#2565: invalid local bytes cannot remain advertised as a local catalog book.

PDFs are synthetic; SQLite and filesystem only. No remote entitlement action.
"""
from __future__ import annotations

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def test_corrupt_existing_pdf_invalidates_catalog_presence_and_recovers(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "book.pdf"
    path.write_bytes(b"%PDF-1.7\nvalid-inventory")
    storage = Storage(tmp_path / "state.sqlite")
    catalog = LocalCatalog(storage, root)

    assert catalog.scan().cataloged == 1
    original_placement = storage.placement_for_path(path)
    placement_id = original_placement["placement_id"]
    original_artifact = original_placement["artifact_id"]
    assert all(row["local_state"] == "local" for row in storage.catalog_entries())

    path.write_bytes(b"not a PDF anymore")
    result = catalog.scan()
    assert result.invalid == 1
    placement = storage.placement_get(placement_id)
    assert placement["state"] == "invalid"
    assert placement["artifact_id"] == original_artifact
    assert placement["metadata"]["invalid_reason"] == "invalid_pdf_header"
    entries = [row for row in storage.catalog_entries()
               if row["placement_id"] == placement_id]
    assert entries and all(row["local_state"] == "invalid" for row in entries)
    assert path.is_file()

    # A valid replacement can be indexed again; no bogus missing transition.
    path.write_bytes(b"%PDF-1.7\nrestored-valid")
    restored = catalog.scan()
    assert restored.cataloged == 1
    assert storage.placement_get(placement_id)["state"] == "present"
    assert all(row["local_state"] == "local" for row in storage.catalog_entries())


def test_first_discovery_of_invalid_pdf_does_not_create_local_catalog(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "bad.pdf"
    path.write_bytes(b"plain text")
    storage = Storage(tmp_path / "state.sqlite")

    assert LocalCatalog(storage, root).scan().invalid == 1
    assert storage.placement_for_path(path)["state"] == "invalid"
    assert not storage.catalog_entries(state="local")
