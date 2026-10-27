"""#2565: local disk rescans must not revoke recorded purchase/loan provenance.

All EPUB/PDF facts below are synthetic local files and SQLite rows.
"""
from __future__ import annotations

from cifereca.acquisition_engine import stable_id
from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _case(tmp_path, *parts, provider="kobo"):
    root = tmp_path / "library"
    root.mkdir()
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\nSynthetic content")
    storage = Storage(tmp_path / "state.sqlite")
    placement_id = storage.placement_put({
        "path": str(path), "artifact_id": None,
        "identity_id": "buyer-a", "provider_id": provider,
        "source_item_id": "edition-123", "state": "present",
        "metadata": {"legacy_note": "provenance already checked"},
    })
    storage.catalog_entry_put({
        "catalog_entry_id": stable_id("catalog", placement_id),
        "placement_id": placement_id, "artifact_id": None,
        "title": "Existing title", "identity_id": "buyer-a",
        "provider_id": provider, "item_id": "edition-123",
        "local_state": "local", "metadata": {"path": str(path)},
    })
    return storage, root, path, placement_id


def _assert_scoped(storage, placement_id):
    placement = storage.placement_get(placement_id)
    assert placement["identity_id"] == "buyer-a"
    assert placement["provider_id"] == "kobo"
    assert placement["source_item_id"] == "edition-123"
    assert placement["state"] == "present"
    entries = [e for e in storage.catalog_entries() if e["placement_id"] == placement_id]
    assert len(entries) == 1
    assert entries[0]["identity_id"] == "buyer-a"
    assert entries[0]["provider_id"] == "kobo"
    assert entries[0]["item_id"] == "edition-123"
    assert entries[0]["local_state"] == "local"


def test_flat_path_without_hints_preserves_existing_owner_provider_and_edition(tmp_path):
    storage, root, path, placement_id = _case(tmp_path, "book.pdf")
    for _ in range(2):
        result = LocalCatalog(storage, root).scan()
        assert result.cataloged == 1
        _assert_scoped(storage, placement_id)
    assert path.is_file()


def test_provider_directory_without_identity_preserves_owner_and_edition(tmp_path):
    storage, root, path, placement_id = _case(tmp_path, "kobo", "book.pdf")
    LocalCatalog(storage, root).scan()
    _assert_scoped(storage, placement_id)
    assert path.is_file()


def test_unrelated_folder_name_does_not_override_existing_supplier_provenance(tmp_path):
    storage, root, path, placement_id = _case(
        tmp_path, "other-provider", "book.pdf",
    )
    for _ in range(2):
        result = LocalCatalog(storage, root).scan()
        assert result.invalid == 0 and result.cataloged == 1
        _assert_scoped(storage, placement_id)
    assert path.is_file()


def test_new_unscoped_file_does_not_invent_purchase_provenance(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "book.pdf"
    path.write_bytes(b"%PDF-1.7\nNew content")
    storage = Storage(tmp_path / "state.sqlite")
    LocalCatalog(storage, root).scan()
    placement = storage.placement_for_path(path)
    assert placement is not None
    assert placement["identity_id"] is None
    assert placement["provider_id"] is None
    assert placement["source_item_id"] is None
