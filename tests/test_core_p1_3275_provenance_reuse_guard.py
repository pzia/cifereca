"""#3275: a recipient's existing bytes are not an A→B transfer receipt.

Exact transfer metadata is necessary to reuse an independent target file.
A legacy A/B hardlink is handled separately by the existing repair test.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_core0925_open_destinations import _placement, _service
from tests.test_core_p1_3275_safe_transfer import _source


@pytest.mark.parametrize("metadata", [
    {"materialized_from": "SOURCE"},
    {"materialized_from": "SOURCE", "transfer_kind": "local_copy"},
    {"materialized_from": "SOURCE", "transfer_kind": "local_copy",
     "file_origin_identity_id": "other-identity"},
    {"materialized_from": "SOURCE", "transfer_kind": "unverified",
     "file_origin_identity_id": "alice"},
])
def test_equal_bytes_with_incomplete_or_foreign_provenance_not_adopted(tmp_path, metadata):
    service = _service(tmp_path)
    src, placement = _source(service, tmp_path)
    owned = tmp_path / "EBooks" / "bob" / "local" / "Book.epub"
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_bytes(src.read_bytes())
    prior_metadata = {
        key: placement if value == "SOURCE" else value
        for key, value in metadata.items()
    }
    old_id = service.storage.placement_put({
        "artifact_id": "sha256:test", "path": str(owned),
        "identity_id": "bob", "provider_id": "local",
        "source_item_id": "book", "state": "present",
        "metadata": prior_metadata,
    })
    service.storage.catalog_entry_put({
        "catalog_entry_id": "independent-b", "artifact_id": "sha256:test",
        "placement_id": old_id, "title": "Existing B",
        "identity_id": "bob", "provider_id": "local", "item_id": "book",
        "local_state": "local",
        "metadata": {"rights": "B-owned-existing"},
    })
    before_rights = service.storage.entitlements()
    before_acquisitions = service.storage.acquisitions()
    try:
        first = service.materialize_to_identity(placement, "bob")
        new_path = Path(first["path"])
        assert first["status"] == "complete"
        assert new_path != owned
        assert new_path.name == "Book (2).epub"
        assert new_path.read_bytes() == owned.read_bytes() == src.read_bytes()
        assert os.stat(src).st_ino != os.stat(new_path).st_ino
        assert os.stat(owned).st_ino != os.stat(new_path).st_ino
        assert service.storage.placement_get(old_id)["metadata"] == prior_metadata
        bob_original = next(row for row in service.storage.catalog_entries()
                            if row.get("catalog_entry_id") == "independent-b")
        assert bob_original["metadata"]["rights"] == "B-owned-existing"
        assert service.storage.entitlements() == before_rights
        assert service.storage.acquisitions() == before_acquisitions
        second = service.materialize_to_identity(placement, "bob")
        assert second["status"] == "unchanged"
        assert second["placement_id"] == first["placement_id"]
        assert second["path"] == first["path"]
        assert len([p for p in service.storage.placements()
                    if p["identity_id"] == "bob"]) == 2
        transfer = service.storage.placement_get(first["placement_id"])
        assert transfer["metadata"]["file_origin_identity_id"] == "alice"
        assert transfer["metadata"]["transfer_kind"] == "local_copy"
    finally:
        service.close()
