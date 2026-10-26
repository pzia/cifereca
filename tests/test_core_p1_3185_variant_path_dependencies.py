"""#3185: physical maintenance cannot strand variant paths.

All records, bytes and metadata are synthetic/local.  No plugin, network,
acquisition or rights mutation.
"""
from __future__ import annotations

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core041_artifact_authority import make_service
from tests.test_core_p0_3185_duplicate_dependencies import _rig


def _variant(storage, artifact, path, *, nested=True):
    metadata = {"output": {"source": [{"path": str(path)}]}} if nested else {"path": str(path)}
    return storage.artifact_variant_put({
        "artifact_id": artifact.artifact_id,
        "source_artifact_id": artifact.artifact_id,
        "source_sha256": artifact.sha256,
        "transformer_plugin_id": "synthetic.test",
        "variant_kind": "derived",
        "metadata": metadata,
    })


def test_variant_path_blocks_duplicate_at_preview(tmp_path):
    service, broker, request, doomed, kept, artifact = _rig(tmp_path)
    try:
        _variant(service.storage, artifact, doomed)
        with pytest.raises(Conflict, match="référencé"):
            broker.plan(request)
        assert doomed.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
    finally:
        service.storage.close()


def test_variant_path_added_after_preview_blocks_duplicate_run(tmp_path):
    service, broker, request, doomed, kept, artifact = _rig(tmp_path)
    try:
        plan = broker.plan(request)
        _variant(service.storage, artifact, doomed, nested=False)
        with pytest.raises(Conflict, match="référencé"):
            broker.run(plan)
        assert doomed.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
    finally:
        service.storage.close()


def test_corrupt_variant_metadata_blocks_destructive_cleanup(tmp_path):
    service, broker, request, doomed, kept, artifact = _rig(tmp_path)
    try:
        variant = _variant(service.storage, artifact, kept)
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE artifact_variants SET metadata_json=? WHERE variant_id=?",
                       ("{malformed LOCAL", variant))
        with pytest.raises(Conflict):
            broker.plan(request)
        assert doomed.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id)
    finally:
        service.storage.close()


def test_unrelated_variant_path_does_not_block_unreferenced_duplicate(tmp_path):
    service, broker, request, doomed, kept, artifact = _rig(tmp_path)
    try:
        _variant(service.storage, artifact, kept)
        outcome = broker.run(broker.plan(request))
        assert outcome.ok and not doomed.exists() and kept.exists()
    finally:
        service.storage.close()


def _rename_case(tmp_path):
    service, artifact, placement = make_service(tmp_path)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(LibraryMutationRequest(
        "normalize_filename", placement_id=placement,
    ))
    return service, artifact, placement, old, new, broker, plan


def test_variant_path_blocks_rename_before_sqlite_changes(tmp_path):
    service, artifact, placement, old, new, broker, plan = _rename_case(tmp_path)
    try:
        _variant(service.storage, artifact, old)
        with pytest.raises(ValueError, match="relocate_variant_reference_requires_relink"):
            broker.run(plan)
        assert old.is_file() and not new.exists()
        assert service.storage.placement_get(placement)["path"] == str(old)
        assert service.storage.artifact_variants()[0]["metadata"]["output"]["source"][0]["path"] == str(old)
    finally:
        service.storage.close()


def test_corrupt_variant_blocks_rename_and_rolls_back_sqlite(tmp_path):
    service, artifact, placement, old, new, broker, plan = _rename_case(tmp_path)
    try:
        variant = _variant(service.storage, artifact, new)
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE artifact_variants SET metadata_json=? WHERE variant_id=?",
                       ('{"output":', variant))
        with pytest.raises(ValueError, match="relocate_variant_context_unavailable"):
            broker.run(plan)
        assert old.is_file() and not new.exists()
        assert service.storage.placement_get(placement)["path"] == str(old)
    finally:
        service.storage.close()


def test_unrelated_variant_path_keeps_normalize_working(tmp_path):
    service, artifact, placement, old, new, broker, plan = _rename_case(tmp_path)
    try:
        _variant(service.storage, artifact, old.with_name("other.epub"))
        outcome = broker.run(plan)
        assert outcome.ok and new.is_file() and not old.exists()
        assert service.storage.placement_get(placement)["path"] == str(new)
    finally:
        service.storage.close()
