"""P0 #3185: deletion of a derived file must follow durable SQLite changes.

Synthetic content, SQLite and filesystem only; never read real licences.
"""
from __future__ import annotations

from pathlib import Path
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
    original_delete = service.storage.placement_delete

    def fail_before_commit(_placement_id):
        raise RuntimeError("synthetic sqlite failure")

    monkeypatch.setattr(service.storage, "placement_delete", fail_before_commit)
    with pytest.raises(RuntimeError, match="sqlite failure"):
        broker.run(plan, Control())
    assert path.read_bytes() == variant_bytes
    assert original.read_bytes() == b"book-data"
    assert service.storage.placement_get(placement["placement_id"]) is not None
    assert service.storage.artifact_variants(artifact_id=derived.artifact_id)
    monkeypatch.setattr(service.storage, "placement_delete", original_delete)
    assert broker.run(plan, Control()).ok
    assert not path.exists()
    assert original.read_bytes() == b"book-data"
    assert service.storage.placement_get(original_id) is not None
    assert not service.storage.artifact_variants(artifact_id=derived.artifact_id)


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
    original_delete = service.storage.placement_delete

    def commit_then_replace(placement_id):
        original_delete(placement_id)
        path.write_bytes(b"changed after sqlite commit")

    monkeypatch.setattr(service.storage, "placement_delete", commit_then_replace)
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
    original_delete = service.storage.placement_delete

    def commit_then_symlink(placement_id):
        original_delete(placement_id)
        path.unlink()
        path.symlink_to(original)

    monkeypatch.setattr(service.storage, "placement_delete", commit_then_symlink)
    result = broker.run(plan, Control())
    assert result.ok and result.warnings
    assert path.is_symlink()
    assert original.read_bytes() == b"book-data"
    assert service.storage.placement_get(placement["placement_id"]) is None


def test_variant_with_catalogue_dependency_is_not_destroyed(tmp_path):
    service, source, derived, original_id, broker, plan, placement, path, original = (
        _variant(tmp_path)
    )
    service.storage.catalog_entry_put({
        "artifact_id": derived.artifact_id,
        "placement_id": placement["placement_id"],
        "identity_id": "id1", "provider_id": "local",
        "item_id": "book-1", "title": "Book", "media_format": "epub",
        "local_state": "local", "metadata": {"path": str(path)},
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
