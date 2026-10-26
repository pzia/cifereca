"""#3185: a placement rename must not reassign contradictory catalog rights.

SQLite and files are synthetic. No provider, download, or real library I/O.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import LibraryMutationRequest
from tests.test_core041_artifact_authority import Control, make_service


def _case(tmp_path):
    service, source, original_id = make_service(tmp_path)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(
        LibraryMutationRequest("normalize_filename", placement_id=original_id)
    )
    return service, source, original_id, old, new, broker, plan


def _catalog_values(service):
    with service.storage._lock, service.storage._connect() as db:
        return db.execute(
            "SELECT catalog_entry_id,placement_id,identity_id,provider_id,"
            "item_id,artifact_id,metadata_json FROM catalog_entries ORDER BY 1"
        ).fetchall()


def _assert_not_reassigned(service, original_id, old, new, before, *, target_present=False):
    assert old.read_bytes() == b"book-data"
    if target_present:
        assert new.read_bytes() == b"book-data"
    else:
        assert not new.exists()
    assert service.storage.placement_get(original_id)["path"] == str(old)
    assert _catalog_values(service) == before


@pytest.mark.parametrize(("field", "foreign"), [
    ("identity_id", "other-identity"),
    ("provider_id", "other-provider"),
    ("item_id", "different-edition"),
    ("artifact_id", "sha256:" + "f" * 64),
])
def test_foreign_catalogue_coordinates_block_rename_before_database_or_file_change(
    tmp_path, field, foreign,
):
    service, _, placement_id, old, new, broker, plan = _case(tmp_path)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            f"UPDATE catalog_entries SET {field}=? WHERE placement_id=?",
            (foreign, placement_id),
        )
    before = _catalog_values(service)
    with pytest.raises(ValueError, match="^relocate_catalog_scope_conflict$"):
        broker.run(plan, Control())
    _assert_not_reassigned(service, placement_id, old, new, before)


@pytest.mark.parametrize(("field", "foreign"), [
    ("identity_id", "other-identity"),
    ("provider_id", "other-provider"),
    ("item_id", "different-edition"),
    ("artifact_id", "sha256:" + "f" * 64),
])
def test_destination_catalogue_provenance_cannot_be_taken_over_on_merge(
    tmp_path, field, foreign,
):
    service, source, placement_id, old, new, broker, plan = _case(tmp_path)
    new.write_bytes(old.read_bytes())
    target_id = service.storage.placement_put({
        "artifact_id": source.artifact_id, "path": str(new),
        "identity_id": "id1", "provider_id": "local",
        "source_item_id": "book-1", "state": "present", "metadata": {},
    })
    service.storage.catalog_entry_put({
        "catalog_entry_id": "target-catalog", "placement_id": target_id,
        "artifact_id": source.artifact_id, "identity_id": "id1",
        "provider_id": "local", "item_id": "book-1",
        "title": "Synthetic target", "local_state": "local",
        "metadata": {"path": str(new)},
    })
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            f"UPDATE catalog_entries SET {field}=? WHERE catalog_entry_id='target-catalog'",
            (foreign,),
        )
    before = _catalog_values(service)
    with pytest.raises(ValueError, match="^relocate_catalog_scope_conflict$"):
        broker.run(plan, Control())
    _assert_not_reassigned(service, placement_id, old, new, before, target_present=True)
    assert service.storage.placement_get(target_id)["path"] == str(new)


def test_matching_catalogue_provenance_still_allows_crash_safe_rename(tmp_path):
    service, source, placement_id, old, new, broker, plan = _case(tmp_path)
    outcome = broker.run(plan, Control())
    assert outcome.ok and outcome.changed == 1
    assert not old.exists()
    assert new.read_bytes() == b"book-data"
    assert service.storage.placement_get(placement_id)["path"] == str(new)
    entries = _catalog_values(service)
    assert len(entries) == 1
    assert entries[0][2:6] == ("id1", "local", "book-1", source.artifact_id)
    assert str(new) in entries[0][-1]


def test_legacy_null_catalogue_fields_do_not_create_a_new_conflict(tmp_path):
    service, _, placement_id, old, new, broker, plan = _case(tmp_path)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE catalog_entries SET identity_id=NULL,provider_id=NULL,"
            "item_id=NULL,artifact_id=NULL WHERE placement_id=?",
            (placement_id,),
        )
    result = broker.run(plan, Control())
    assert result.ok and result.changed == 1
    assert new.read_bytes() == b"book-data" and not old.exists()
