"""P0 #3185: deletion of a derived file must follow durable SQLite changes.

Synthetic content, SQLite and filesystem only; never read real licences.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import shutil

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl, ArtifactExecutionBrokerImpl
from cifereca.plugin_api import Conflict, LibraryMutationRequest, TransformRequest
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)


def _variant(tmp_path):
    service, source, original_id = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=FakeTransformer())
    derived = ArtifactExecutionBrokerImpl(service).execute_transform(
        TransformRequest(source, "optimize"), control=Control(),
    )
    broker = LibraryMaintenanceBrokerImpl(service)
    request = LibraryMutationRequest("delete_variant", artifact_id=derived.artifact_id)
    plan = broker.plan(request)
    placement, = [
        row for row in service.storage.placements()
        if row["artifact_id"] == derived.artifact_id
    ]
    path = Path(placement["path"])
    original = tmp_path / "library" / "Book.epub"
    assert original.is_file() and path.is_file()
    return service, source, derived, original_id, broker, plan, placement, path, original


def test_sql_failure_never_unlinks_variant_or_source_and_retry_is_safe(
    tmp_path, monkeypatch,
):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    variant_bytes = path.read_bytes()
    original_retire = service.storage.artifact_variant_retire_atomic

    def fail_before_commit(**_kwargs):
        raise RuntimeError("synthetic sqlite failure")

    monkeypatch.setattr(service.storage, "artifact_variant_retire_atomic", fail_before_commit)
    with pytest.raises(RuntimeError, match="sqlite failure"):
        broker.run(plan, Control())
    assert path.read_bytes() == variant_bytes
    assert original.read_bytes() == b"book-data"
    assert service.storage.placement_get(placement["placement_id"]) is not None
    assert service.storage.artifact_variants(artifact_id=derived.artifact_id)
    monkeypatch.setattr(service.storage, "artifact_variant_retire_atomic", original_retire)
    assert broker.run(plan, Control()).ok
    assert not path.exists()
    assert original.read_bytes() == b"book-data"
    assert service.storage.placement_get(original_id) is not None
    assert not service.storage.artifact_variants(artifact_id=derived.artifact_id)



def test_sqlite_failure_after_placement_delete_rolls_back_all_references(tmp_path):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    preference = f"artifact.variant.preferred.{source.artifact_id}.read"
    service.storage.metadata_set(preference, derived.artifact_id)
    before = path.read_bytes()
    # SQLite aborts AFTER the first DELETE, while retiring the variant:
    # one transaction must restore catalogue, placement, variant and prefs.
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "CREATE TRIGGER test_abort_variant_retire "
            "BEFORE DELETE ON artifact_variants BEGIN "
            "SELECT RAISE(ABORT, 'synthetic variant retire failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic variant retire failed"):
        broker.run(plan, Control())
    assert path.read_bytes() == before
    assert original.read_bytes() == b"book-data"
    assert service.storage.placement_get(placement["placement_id"]) is not None
    assert service.storage.artifact_variants(artifact_id=derived.artifact_id)
    assert service.storage.metadata_get(preference) == derived.artifact_id
    with service.storage._lock, service.storage._connect() as db:
        db.execute("DROP TRIGGER test_abort_variant_retire")
    assert broker.run(plan, Control()).ok
    assert not path.exists()
    assert service.storage.metadata_get(preference) == ""
    assert service.storage.placement_get(original_id) is not None


def test_new_sqlite_dependency_between_preview_and_commit_blocks_unlink(
    tmp_path, monkeypatch,
):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    original_retire = service.storage.artifact_variant_retire_atomic

    def concurrent_reference(**kwargs):
        # Emulate another writer after the broker's read-only preflight,
        # before BEGIN IMMEDIATE. The transaction must recheck dependencies.
        service.storage.catalog_entry_put({
            "artifact_id": source.artifact_id, "placement_id": None,
            "identity_id": "id1", "provider_id": "local",
            "item_id": "second-right", "title": "Another right",
            "media_format": "epub", "local_state": "remote_catalog",
            "metadata": {"path": str(path)},
        })
        return original_retire(**kwargs)

    monkeypatch.setattr(
        service.storage, "artifact_variant_retire_atomic", concurrent_reference,
    )
    with pytest.raises(ValueError, match="variant_retire_references_changed"):
        broker.run(plan, Control())
    assert path.is_file() and original.is_file()
    assert service.storage.placement_get(placement["placement_id"]) is not None
    assert service.storage.artifact_variants(artifact_id=derived.artifact_id)

def test_replaced_bytes_after_preview_cannot_be_unlinked(tmp_path):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    path.write_bytes(b"a different, unrelated document")
    with pytest.raises(Conflict, match="Octets de variante non vérifiés"):
        broker.run(plan, Control())
    assert path.read_bytes() == b"a different, unrelated document"
    assert service.storage.placement_get(placement["placement_id"]) is not None
    assert service.storage.artifact_variants(artifact_id=derived.artifact_id)
    assert original.read_bytes() == b"book-data"


def test_symlink_substitution_after_preview_never_unlinks_target(tmp_path):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    path.unlink()
    path.symlink_to(original)
    with pytest.raises(Conflict):
        broker.run(plan, Control())
    assert path.is_symlink()
    assert original.read_bytes() == b"book-data"
    assert service.storage.placement_get(placement["placement_id"]) is not None
    assert service.storage.artifact_variants(artifact_id=derived.artifact_id)


def test_file_replaced_during_sql_commit_is_preserved_with_warning(
    tmp_path, monkeypatch,
):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    original_retire = service.storage.artifact_variant_retire_atomic

    def commit_then_replace(**kwargs):
        original_retire(**kwargs)
        path.write_bytes(b"changed after sqlite commit")

    monkeypatch.setattr(service.storage, "artifact_variant_retire_atomic", commit_then_replace)
    result = broker.run(plan, Control())
    assert result.ok and result.warnings
    assert path.read_bytes() == b"changed after sqlite commit"
    assert service.storage.placement_get(placement["placement_id"]) is None
    assert not service.storage.artifact_variants(artifact_id=derived.artifact_id)
    assert original.read_bytes() == b"book-data"


def test_symlink_inserted_during_sql_commit_is_not_unlinked(tmp_path, monkeypatch):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    original_retire = service.storage.artifact_variant_retire_atomic

    def commit_then_symlink(**kwargs):
        original_retire(**kwargs)
        path.unlink()
        path.symlink_to(original)

    monkeypatch.setattr(service.storage, "artifact_variant_retire_atomic", commit_then_symlink)
    result = broker.run(plan, Control())
    assert result.ok and result.warnings
    assert path.is_symlink()
    assert original.read_bytes() == b"book-data"
    assert service.storage.placement_get(placement["placement_id"]) is None


def test_variant_with_catalogue_dependency_is_not_destroyed(tmp_path):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    # An independent notice with no placement still references this file.
    # The generated derived catalogue row is its own removable projection,
    # but this second row must never be silently erased or stranded.
    service.storage.catalog_entry_put({
        "artifact_id": source.artifact_id,
        "placement_id": None,
        "identity_id": "id1", "provider_id": "local",
        "item_id": "independent-book", "title": "Independent notice",
        "media_format": "epub", "local_state": "remote_catalog",
        "metadata": {"path": str(path)},
    })
    with pytest.raises(Conflict, match="Références de variante"):
        broker.run(plan, Control())
    assert path.is_file()
    assert original.read_bytes() == b"book-data"
    assert service.storage.placement_get(placement["placement_id"]) is not None
    assert service.storage.artifact_variants(artifact_id=derived.artifact_id)


def test_multi_placement_deletion_is_refused_before_any_partial_transaction(
    tmp_path,
):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    second = tmp_path / "another-variant.epub"
    shutil.copyfile(path, second)
    second_id = service.storage.placement_put({
        "artifact_id": derived.artifact_id, "path": str(second),
        "identity_id": "id1", "provider_id": "local",
        "source_item_id": "book-1", "state": "present",
        "metadata": {
            "variant": True, "source_artifact_id": source.artifact_id,
        },
    })
    with pytest.raises(Conflict, match="placements multiples"):
        broker.run(plan, Control())
    assert path.is_file() and second.is_file()
    assert service.storage.placement_get(placement["placement_id"]) is not None
    assert service.storage.placement_get(second_id) is not None
    assert service.storage.artifact_variants(artifact_id=derived.artifact_id)
    assert original.read_bytes() == b"book-data"
