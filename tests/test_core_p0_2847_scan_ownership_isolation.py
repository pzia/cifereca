"""#2847/#2565: a rescan must never invent an owner transfer.

Every EPUB/PDF file, profile and account is synthetic and offline.
"""
from __future__ import annotations

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _case(tmp_path, *, owner="identity-a", provider="paris"):
    library = tmp_path / "library"
    file = library / "identity-b" / "paris" / "Book.pdf"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"%PDF-1.7\nSynthetic only\n")
    store = Storage(tmp_path / "storage.sqlite")
    store.identity_put({
        "identity_id": "identity-a", "directory_name": "identity-a",
        "label": "A",
    })
    store.identity_put({
        "identity_id": "identity-b", "directory_name": "identity-b",
        "label": "B",
    })
    placement_id = store.placement_put({
        "artifact_id": "sha256:historic-artifact",
        "path": str(file), "identity_id": owner,
        "provider_id": provider, "source_item_id": "historical-edition",
        "state": "present", "metadata": {"source": "historic"},
    })
    store.catalog_entry_put({
        "catalog_entry_id": "historical-catalogue",
        "artifact_id": "sha256:historic-artifact",
        "placement_id": placement_id, "title": "Historical title",
        "identity_id": owner, "provider_id": provider,
        "item_id": "historical-edition", "local_state": "local",
        "media_format": "pdf", "metadata": {"path": str(file), "source": "historic"},
    })
    return store, library, placement_id, file


@pytest.mark.parametrize(("owner", "provider"), [
    ("identity-a", "paris"),
    ("identity-b", "numilog"),
    ("legacy-folder-alias", "paris"),
])
def test_rescan_does_not_reassign_existing_owner_or_provider(
    tmp_path, owner, provider,
):
    store, library, placement_id, file = _case(
        tmp_path, owner=owner, provider=provider,
    )

    result = LocalCatalog(store, library).scan()

    assert result.invalid == 1
    assert result.cataloged == 0
    assert file.read_bytes() == b"%PDF-1.7\nSynthetic only\n"
    placement = store.placement_get(placement_id)
    assert placement["identity_id"] == owner
    assert placement["provider_id"] == provider
    assert placement["source_item_id"] == "historical-edition"
    assert placement["artifact_id"] == "sha256:historic-artifact"
    assert placement["state"] == "invalid"
    assert placement["metadata"]["source"] == "historic"
    assert placement["metadata"]["invalid_reason"] == "placement_scope_conflict"
    catalogue = store.catalog_entry_get("historical-catalogue")
    assert catalogue["identity_id"] == owner
    assert catalogue["provider_id"] == provider
    assert catalogue["item_id"] == "historical-edition"
    assert catalogue["local_state"] == "invalid"
    assert catalogue["metadata"]["path"] == str(file)
    assert store.acquisitions() == []


def test_rescan_keeps_existing_matching_scope_and_local_state(tmp_path):
    store, library, placement_id, file = _case(
        tmp_path, owner="identity-b", provider="paris",
    )

    result = LocalCatalog(store, library).scan()

    assert result.cataloged == 1 and result.invalid == 0
    placement = store.placement_get(placement_id)
    assert placement["identity_id"] == "identity-b"
    assert placement["provider_id"] == "paris"
    assert placement["source_item_id"] == "historical-edition"
    assert placement["state"] == "present"
    assert file.is_file()


def test_rescan_can_assign_unowned_file_without_reassigning_existing_owner(tmp_path):
    store, library, placement_id, _ = _case(
        tmp_path, owner=None, provider=None,
    )

    result = LocalCatalog(store, library).scan()

    assert result.cataloged == 1 and result.invalid == 0
    placement = store.placement_get(placement_id)
    assert placement["identity_id"] == "identity-b"
    assert placement["provider_id"] == "paris"
    assert placement["state"] == "present"
