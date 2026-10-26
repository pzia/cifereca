"""P0 #2565/#2847: failed targeted reindex preserves history, never false local presence.

Only synthetic files and a real temporary SQLite database; no network/provider.
"""
from __future__ import annotations

from cifereca.acquisition_engine import stable_id
from cifereca.catalog import LocalCatalog
from cifereca.plugin_api import BUNDLE_MANIFEST_NAME, write_bundle_manifest
from cifereca.storage import Storage


def _case(tmp_path, name="book.pdf"):
    root = tmp_path / "library"
    root.mkdir()
    file = root / name
    file.write_bytes(b"%PDF-1.7\nSynthetic file\n")
    store = Storage(tmp_path / "state.sqlite")
    catalogue = LocalCatalog(store, root)
    result = catalogue.index_path(
        file, identity_id="owner-a", provider_id="kobo", item_id="edition-1",
        title="Existing purchase",
    )
    assert result["status"] == "indexed"
    placed = store.placement_for_path(file)
    store.placement_put({
        **placed,
        "metadata": {**placed["metadata"], "historic_source": "original"},
    })
    return catalogue, store, file, placed["placement_id"], result["artifact_id"]


def test_targeted_invalid_pdf_retains_historic_artifact_and_edition(tmp_path):
    catalogue, store, file, placement_id, original_artifact = _case(tmp_path)
    file.write_bytes(b"replaced with non-PDF content")
    result = catalogue.index_path(file)

    assert result == {
        "status": "invalid", "path": str(file),
        "reason": "invalid_pdf_header", "media_format": "pdf",
    }
    placed = store.placement_get(placement_id)
    assert placed["state"] == "invalid"
    assert placed["artifact_id"] == original_artifact
    assert (placed["identity_id"], placed["provider_id"], placed["source_item_id"]) == (
        "owner-a", "kobo", "edition-1",
    )
    assert placed["metadata"]["historic_source"] == "original"
    assert placed["metadata"]["invalid_reason"] == "invalid_pdf_header"
    entry = store.catalog_entry_get(stable_id("catalog", placement_id))
    assert (entry["title"], entry["item_id"], entry["local_state"]) == (
        "Existing purchase", "edition-1", "invalid",
    )
    assert entry["artifact_id"] == original_artifact
    assert store.artifact_get(original_artifact) is not None
    assert store.acquisitions() == []


def test_targeted_invalid_marks_every_projection_of_the_same_placement(tmp_path):
    catalogue, store, file, placement_id, _ = _case(tmp_path)
    store.catalog_entry_put({
        "catalog_entry_id": "additional-projection",
        "placement_id": placement_id, "title": "Another view",
        "identity_id": "owner-a", "provider_id": "kobo",
        "item_id": "edition-1", "local_state": "local",
        "metadata": {"path": str(file)},
    })
    file.write_bytes(b"not a PDF")
    assert catalogue.index_path(file)["status"] == "invalid"
    assert {
        entry["local_state"]
        for entry in store.catalog_entries()
        if entry["placement_id"] == placement_id
    } == {"invalid"}


def test_targeted_reindex_after_repair_restores_physical_state(tmp_path):
    catalogue, store, file, placement_id, _ = _case(tmp_path)
    file.write_bytes(b"broken")
    assert catalogue.index_path(file)["status"] == "invalid"

    file.write_bytes(b"%PDF-1.7\nRepaired synthetic file")
    result = catalogue.index_path(file)
    assert result["status"] == "indexed"
    assert store.placement_get(placement_id)["state"] == "present"
    assert store.catalog_entry_get(stable_id("catalog", placement_id))["local_state"] == "local"
    assert store.placement_get(placement_id)["source_item_id"] == "edition-1"


def test_new_invalid_file_creates_no_owned_catalogue_entry(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    file = root / "invalid.pdf"
    file.write_bytes(b"broken")
    store = Storage(tmp_path / "state.sqlite")
    result = LocalCatalog(store, root).index_path(file)
    assert result["status"] == "invalid"
    assert store.placement_for_path(file)["artifact_id"] is None
    assert store.catalog_entries() == []


def test_invalid_bundle_manifest_revokes_stale_local_projection_only(tmp_path):
    root = tmp_path / "library"
    bundle = root / "audio"
    bundle.mkdir(parents=True)
    (bundle / "track.mp3").write_bytes(b"synthetic audio")
    write_bundle_manifest(bundle, title="Owned audiobook")
    store = Storage(tmp_path / "state.sqlite")
    catalogue = LocalCatalog(store, root)
    assert catalogue.index_path(
        bundle, identity_id="owner-a", provider_id="baen", item_id="audio-1"
    )["status"] == "indexed"
    placed = store.placement_for_path(bundle)
    artifact_id = placed["artifact_id"]
    (bundle / BUNDLE_MANIFEST_NAME).write_text("{invalid JSON", encoding="utf-8")

    result = catalogue.index_path(bundle)
    assert result["reason"] == "bundle_manifest_invalid"
    after = store.placement_get(placed["placement_id"])
    assert after["artifact_id"] == artifact_id
    assert after["state"] == "invalid"
    assert (after["identity_id"], after["provider_id"], after["source_item_id"]) == (
        "owner-a", "baen", "audio-1",
    )
    row = store.catalog_entry_get(stable_id("catalog", placed["placement_id"]))
    assert row["local_state"] == "invalid"
    assert row["artifact_id"] == artifact_id
    assert store.acquisitions() == []
