"""#3185: explicit filename normalization preserves every live SQLite path.

All identities, filenames, scopes and bytes are synthetic and offline.
"""
from __future__ import annotations

import json

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import LibraryMutationRequest
from tests.test_core041_artifact_authority import Control, make_service


def _case(tmp_path):
    service, _, placement_id = make_service(tmp_path)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(
        LibraryMutationRequest("normalize_filename", placement_id=placement_id),
    )
    return service, placement_id, old, new, broker, plan


def _placement_metadata(service, placement_id, metadata):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE placements SET metadata_json=? WHERE placement_id=?",
            (json.dumps(metadata), placement_id),
        )


def _assert_rejected_unchanged(service, placement_id, old, new):
    assert old.read_bytes() == b"book-data"
    assert not new.exists()
    assert service.storage.placement_get(placement_id)["path"] == str(old)
    entries = [
        row for row in service.storage.catalog_entries()
        if row["placement_id"] == placement_id
    ]
    assert len(entries) == 1
    assert entries[0]["metadata"]["path"] == str(old)


def test_normalization_rewrites_relative_path_and_catalogue_in_same_transaction(tmp_path):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    metadata = {
        "relative_path": "Book.epub", "file_size": 9,
        "mtime_ns": 1234, "artifact_kind": "file",
    }
    _placement_metadata(service, placement_id, metadata)
    outcome = broker.run(plan, Control())
    assert outcome.ok and outcome.changed == 1
    assert not old.exists() and new.read_bytes() == b"book-data"
    placement = service.storage.placement_get(placement_id)
    assert placement["path"] == str(new)
    assert placement["metadata"] == {
        **metadata, "relative_path": "Author - Book.epub",
    }
    entries = [
        row for row in service.storage.catalog_entries()
        if row["placement_id"] == placement_id
    ]
    assert len(entries) == 1
    assert entries[0]["metadata"]["path"] == str(new)


@pytest.mark.parametrize("relative", [
    "wrong.epub",
    "other/Unrelated.epub",
    "../Book.epub",
    "/absolute/Book.epub",
])
def test_ambiguous_relative_path_rejected_before_sqlite_or_unlink(
    tmp_path, relative,
):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    _placement_metadata(service, placement_id, {
        "relative_path": relative, "file_size": 9,
    })
    with pytest.raises(ValueError, match="relocate_relative_path_requires_relink"):
        broker.run(plan, Control())
    _assert_rejected_unchanged(service, placement_id, old, new)
    assert service.storage.placement_get(placement_id)["metadata"]["relative_path"] == relative


def test_independent_catalogue_path_reference_blocks_rename(tmp_path):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    service.storage.catalog_entry_put({
        "catalog_entry_id": "independent-entry", "placement_id": None,
        "artifact_id": None, "title": "Another book",
        "identity_id": "other-identity", "provider_id": "kobo",
        "item_id": "other-item", "local_state": "local",
        "metadata": {"path": str(old)},
    })
    with pytest.raises(ValueError, match="relocate_catalog_reference_requires_relink"):
        broker.run(plan, Control())
    _assert_rejected_unchanged(service, placement_id, old, new)
    assert service.storage.catalog_entry_get("independent-entry")["metadata"]["path"] == str(old)


def test_nested_catalogue_checkpoint_blocks_rename(tmp_path):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE catalog_entries SET metadata_json=? WHERE placement_id=?",
            (json.dumps({
                "path": str(old),
                "checkpoint": {"delivery": [str(old)]},
            }), placement_id),
        )
    with pytest.raises(ValueError, match="relocate_catalog_reference_requires_relink"):
        broker.run(plan, Control())
    _assert_rejected_unchanged(service, placement_id, old, new)


def test_broken_catalogue_metadata_cannot_authorize_rename(tmp_path):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO catalog_entries(catalog_entry_id,title,local_state,metadata_json,"
            "created_at,updated_at) VALUES(?,?,?, ?,?,?)",
            ("broken-independent-entry", "Synthetic", "local", "{bad-json", 1, 1),
        )
    with pytest.raises(ValueError, match="relocate_catalog_context_unavailable"):
        broker.run(plan, Control())
    _assert_rejected_unchanged(service, placement_id, old, new)


def test_noop_relocation_does_not_require_new_relative_metadata(tmp_path):
    service, placement_id, old, new, _, _ = _case(tmp_path)
    service.storage.relocate_local_path(
        old, old, identity_id="id1", provider_id="local",
    )
    assert service.storage.placement_get(placement_id)["path"] == str(old)
    assert not new.exists()
