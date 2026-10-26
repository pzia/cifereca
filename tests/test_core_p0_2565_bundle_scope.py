"""#2565: reindexing a local bundle never revokes existing ownership.

Synthetic audio bytes, bundle manifest and SQLite only. No provider I/O.
"""
from __future__ import annotations

import pytest

from cifereca.acquisition_engine import stable_id
from cifereca.catalog import LocalCatalog
from cifereca.plugin_api import BUNDLE_MANIFEST_NAME, write_bundle_manifest
from cifereca.storage import Storage


def _case(tmp_path):
    root = tmp_path / "library"
    bundle = root / "audio-book"
    bundle.mkdir(parents=True)
    (bundle / "track.mp3").write_bytes(b"synthetic audio")
    write_bundle_manifest(bundle, title="Recorded story")
    storage = Storage(tmp_path / "state.sqlite")
    placement_id = storage.placement_put({
        "path": str(bundle), "artifact_id": "sha256:old-historic-artifact",
        "identity_id": "buyer-a", "provider_id": "gallica",
        "source_item_id": "audio-edition", "state": "present",
        "metadata": {"receipt_context": "original"},
    })
    storage.catalog_entry_put({
        "catalog_entry_id": stable_id("catalog", placement_id),
        "placement_id": placement_id,
        "artifact_id": "sha256:old-historic-artifact",
        "identity_id": "buyer-a", "provider_id": "gallica",
        "item_id": "audio-edition", "title": "Recorded story",
        "local_state": "local", "metadata": {"path": str(bundle)},
    })
    return LocalCatalog(storage, root), storage, bundle, placement_id


def _assert_owner(storage, placement_id):
    placement = storage.placement_get(placement_id)
    assert (placement["identity_id"], placement["provider_id"], placement["source_item_id"]) == (
        "buyer-a", "gallica", "audio-edition",
    )
    entries = [row for row in storage.catalog_entries() if row["placement_id"] == placement_id]
    assert len(entries) == 1
    assert (entries[0]["identity_id"], entries[0]["provider_id"], entries[0]["item_id"]) == (
        "buyer-a", "gallica", "audio-edition",
    )


def test_bundle_global_rescan_preserves_scoped_owner_and_edition(tmp_path):
    catalog, storage, bundle, placement_id = _case(tmp_path)
    for _ in range(2):
        assert catalog.scan().cataloged == 1
        _assert_owner(storage, placement_id)
    assert (bundle / "track.mp3").exists()


def test_bundle_targeted_reindex_without_hints_retains_scope(tmp_path):
    catalog, storage, bundle, placement_id = _case(tmp_path)
    assert catalog.index_path(bundle)["status"] == "indexed"
    _assert_owner(storage, placement_id)


@pytest.mark.parametrize("kwargs", [
    {"identity_id": "another-buyer"},
    {"provider_id": "another-provider"},
    {"item_id": "another-edition"},
])
def test_bundle_rejects_unapproved_owner_or_edition_transfer(tmp_path, kwargs):
    catalog, storage, bundle, placement_id = _case(tmp_path)
    before = storage.placement_get(placement_id)
    before_catalog = storage.catalog_entries()
    result = catalog.index_path(bundle, **kwargs)
    assert result["status"] == "invalid"
    assert result["reason"] == "placement_scope_conflict"
    assert storage.placement_get(placement_id) == before
    assert storage.catalog_entries() == before_catalog
    assert (bundle / "track.mp3").is_file()


def test_invalid_bundle_manifest_does_not_erase_historic_artifact_or_scope(tmp_path):
    catalog, storage, bundle, placement_id = _case(tmp_path)
    (bundle / BUNDLE_MANIFEST_NAME).write_text("{invalid-json", encoding="utf-8")
    assert catalog.index_path(bundle)["status"] == "invalid"
    row = storage.placement_get(placement_id)
    assert row["state"] == "invalid"
    assert row["artifact_id"] == "sha256:old-historic-artifact"
    assert row["metadata"]["receipt_context"] == "original"
    assert row["metadata"]["invalid_reason"] == "bundle_manifest_invalid"
    _assert_owner(storage, placement_id)


def test_new_bundle_without_scope_creates_no_owner_or_edition(tmp_path):
    root = tmp_path / "library"
    bundle = root / "audio-book"
    bundle.mkdir(parents=True)
    (bundle / "track.mp3").write_bytes(b"new bytes")
    write_bundle_manifest(bundle, title="New story")
    storage = Storage(tmp_path / "state.sqlite")
    assert LocalCatalog(storage, root).index_path(bundle)["status"] == "indexed"
    row = storage.placement_for_path(bundle)
    assert row["identity_id"] is None
    assert row["provider_id"] is None
    assert row["source_item_id"] is None
