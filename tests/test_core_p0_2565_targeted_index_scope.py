"""#2565: targeted catalog re-index must not silently transfer acquisition ownership.

Local synthetic PDF bytes + SQLite only; no provider, auth or DRM I/O.
"""
from __future__ import annotations

import pytest

from cifereca.acquisition_engine import stable_id
from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _case(tmp_path, *parts):
    root = tmp_path / "library"
    root.mkdir()
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\nSynthetic")
    storage = Storage(tmp_path / "state.sqlite")
    placement_id = storage.placement_put({
        "path": str(path), "artifact_id": None,
        "identity_id": "buyer-a", "provider_id": "kobo",
        "source_item_id": "edition-a", "state": "present",
        "metadata": {"original": "keep"},
    })
    storage.catalog_entry_put({
        "catalog_entry_id": stable_id("catalog", placement_id),
        "placement_id": placement_id, "artifact_id": None,
        "identity_id": "buyer-a", "provider_id": "kobo",
        "item_id": "edition-a", "title": "A book",
        "local_state": "local", "metadata": {"path": str(path)},
    })
    return LocalCatalog(storage, root), storage, path, placement_id


def test_targeted_index_without_hints_retains_owner_provider_and_edition(tmp_path):
    catalog, storage, path, placement_id = _case(tmp_path, "book.pdf")
    for _ in range(2):
        assert catalog.index_path(path)["status"] == "indexed"
        row = storage.placement_get(placement_id)
        assert (row["identity_id"], row["provider_id"], row["source_item_id"]) == (
            "buyer-a", "kobo", "edition-a",
        )
        entries = [x for x in storage.catalog_entries() if x["placement_id"] == placement_id]
        assert len(entries) == 1
        assert (entries[0]["identity_id"], entries[0]["provider_id"], entries[0]["item_id"]) == (
            "buyer-a", "kobo", "edition-a",
        )


@pytest.mark.parametrize("kwargs", [
    {"identity_id": "another-buyer"},
    {"provider_id": "another-provider"},
    {"item_id": "another-edition"},
])
def test_targeted_reindex_rejects_explicit_scope_conflicts_without_mutation(tmp_path, kwargs):
    catalog, storage, path, placement_id = _case(tmp_path, "book.pdf")
    before = storage.placement_get(placement_id)
    before_catalog = storage.catalog_entries()
    result = catalog.index_path(path, **kwargs)
    assert result["status"] == "invalid"
    assert result["reason"] == "placement_scope_conflict"
    assert storage.placement_get(placement_id) == before
    assert storage.catalog_entries() == before_catalog
    assert path.read_bytes() == b"%PDF-1.7\nSynthetic"


def test_targeted_index_ignores_non_authoritative_provider_directory(tmp_path):
    catalog, storage, path, placement_id = _case(
        tmp_path, "other-provider", "book.pdf",
    )
    result = catalog.index_path(path)
    assert result["status"] == "indexed"
    row = storage.placement_get(placement_id)
    assert row["provider_id"] == "kobo"
    assert row["identity_id"] == "buyer-a"
    assert row["source_item_id"] == "edition-a"


def test_new_file_without_hints_remains_unowned(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "new.pdf"
    path.write_bytes(b"%PDF-1.7\nNew")
    storage = Storage(tmp_path / "state.sqlite")
    assert LocalCatalog(storage, root).index_path(path)["status"] == "indexed"
    row = storage.placement_for_path(path)
    assert row["identity_id"] is None
    assert row["provider_id"] is None
    assert row["source_item_id"] is None
