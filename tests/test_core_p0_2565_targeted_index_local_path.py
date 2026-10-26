"""P0 #2565: targeted physical evidence must be a real file inside the library.

All documents and identities are synthetic; no provider requests or DRM.
"""
from __future__ import annotations

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


@pytest.mark.parametrize("source", ["outside", "file_symlink", "parent_symlink", "inside_symlink"])
def test_targeted_index_cannot_claim_outside_or_linked_file(
    tmp_path, source,
):
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "external"
    outside.mkdir()
    external_pdf = outside / "External.pdf"
    external_pdf.write_bytes(b"%PDF-1.7\nEXTERNAL\n")
    internal_pdf = root / "Owned.pdf"
    internal_pdf.write_bytes(b"%PDF-1.7\nINTERNAL\n")
    if source == "outside":
        selected = external_pdf
    elif source == "file_symlink":
        selected = root / "Linked.pdf"
        selected.symlink_to(external_pdf)
    elif source == "parent_symlink":
        (root / "linked-folder").symlink_to(outside, target_is_directory=True)
        selected = root / "linked-folder" / "External.pdf"
    else:
        selected = root / "InternalLinked.pdf"
        selected.symlink_to(internal_pdf)

    store = Storage(tmp_path / "db.sqlite")
    catalogue = LocalCatalog(store, root)
    result = catalogue.index_path(
        selected, identity_id="account-a", provider_id="kobo", item_id="purchase-a",
    )
    assert result["status"] == "invalid"
    assert result["reason"] == "catalog_index_path_not_local"
    assert store.placements() == []
    assert store.catalog_entries() == []
    assert store.artifacts() == []
    assert store.acquisitions() == []
    assert external_pdf.read_bytes() == b"%PDF-1.7\nEXTERNAL\n"
    assert internal_pdf.read_bytes() == b"%PDF-1.7\nINTERNAL\n"


def test_regular_file_under_library_still_indexes_with_exact_scope(tmp_path):
    root = tmp_path / "library"
    file = root / "account-a" / "kobo" / "Book.pdf"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"%PDF-1.7\nSYNTHETIC\n")
    store = Storage(tmp_path / "db.sqlite")
    result = LocalCatalog(store, root).index_path(
        file, identity_id="account-a", provider_id="kobo", item_id="purchase-a",
    )
    assert result["status"] == "indexed"
    row = store.placement_for_path(file)
    assert row["state"] == "present"
    assert (row["identity_id"], row["provider_id"], row["source_item_id"]) == (
        "account-a", "kobo", "purchase-a",
    )


def test_explicitly_configured_symlink_library_root_remains_supported(tmp_path):
    actual = tmp_path / "physical-library"
    actual.mkdir()
    linked_root = tmp_path / "library-link"
    linked_root.symlink_to(actual, target_is_directory=True)
    file = linked_root / "Book.pdf"
    file.write_bytes(b"%PDF-1.7\nSYNTHETIC\n")
    store = Storage(tmp_path / "db.sqlite")

    result = LocalCatalog(store, linked_root).index_path(file)
    assert result["status"] == "indexed"
    assert store.placement_for_path(actual / "Book.pdf")["state"] == "present"


def test_targeted_bundle_symlink_cannot_claim_external_directory(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    external = tmp_path / "external-bundle"
    external.mkdir()
    (root / "LinkedBundle").symlink_to(external, target_is_directory=True)
    store = Storage(tmp_path / "db.sqlite")

    result = LocalCatalog(store, root).index_path(
        root / "LinkedBundle", identity_id="owner-a",
    )
    assert result["status"] == "invalid"
    assert result["reason"] == "catalog_index_path_not_local"
    assert store.placements() == []
