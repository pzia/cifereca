"""P0 #3185: a content-identical derivative must never delete its source.

Only synthetic files and local SQLite. No plugin provider or remote I/O.
"""
from __future__ import annotations

import hashlib

import pytest

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl, LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import (
    ArtifactFormat, Conflict, LibraryMutationRequest, TransformRequest, TransformResult,
)
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)


class IdenticalTransformer(FakeTransformer):
    def transform(self, request, source, destination, control):
        self.transform_calls += 1
        original = source.path.read_bytes()
        destination.path.write_bytes(original)
        return TransformResult(
            ArtifactFormat("application/epub+zip", "epub"),
            "Optimized.epub", hashlib.sha256(original).hexdigest(), len(original),
        )


def test_identity_transform_reuses_original_without_creating_deletable_variant(tmp_path):
    service, source, placement_id = make_service(tmp_path)
    transformer = IdenticalTransformer()
    service.plugin_manager = FakeManager(transformer=transformer)
    result = ArtifactExecutionBrokerImpl(service).execute_transform(
        TransformRequest(source, "optimize"), control=Control(),
    )
    assert result.artifact_id == source.artifact_id
    assert result.sha256 == source.sha256
    assert transformer.transform_calls == 1
    assert service.storage.artifact_variants(artifact_id=source.artifact_id) == []
    assert service.storage.placement_get(placement_id)["artifact_id"] == source.artifact_id
    assert len(service.storage.placements()) == 1
    assert len(service.storage.artifacts()) == 1
    assert (tmp_path / "library" / "Book.epub").read_bytes() == b"book-data"
    assert not (tmp_path / "library" / "Optimized.epub").exists()


def _self_variant(service, source):
    return service.storage.artifact_variant_put({
        "variant_id": "historical-self-variant",
        "artifact_id": source.artifact_id,
        "source_artifact_id": source.artifact_id,
        "source_sha256": source.sha256,
        "transformer_plugin_id": "synthetic-legacy",
        "variant_kind": "derived", "options_fingerprint": "same-content",
    })


def test_legacy_self_variant_cannot_plan_deletion_of_original_placement(tmp_path):
    service, source, placement_id = make_service(tmp_path)
    _self_variant(service, source)
    broker = LibraryMaintenanceBrokerImpl(service)
    with pytest.raises(Conflict) as caught:
        broker.plan(LibraryMutationRequest("delete_variant", artifact_id=source.artifact_id))
    assert caught.value.code == "artifact_variant_scope_conflict"
    assert service.storage.placement_get(placement_id)["path"] == str(
        tmp_path / "library" / "Book.epub"
    )
    assert (tmp_path / "library" / "Book.epub").read_bytes() == b"book-data"
    assert len(service.storage.artifact_variants(artifact_id=source.artifact_id)) == 1


def test_variant_changed_to_self_reference_after_plan_is_rechecked_before_unlink(tmp_path):
    service, source, original_id = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=FakeTransformer())
    derivative = ArtifactExecutionBrokerImpl(service).execute_transform(
        TransformRequest(source, "optimize"), control=Control(),
    )
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(
        LibraryMutationRequest("delete_variant", artifact_id=derivative.artifact_id)
    )
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE artifact_variants SET source_artifact_id=artifact_id "
            "WHERE artifact_id=?",
            (derivative.artifact_id,),
        )
    with pytest.raises(Conflict) as caught:
        broker.run(plan, Control())
    assert caught.value.code == "artifact_variant_scope_conflict"
    assert service.storage.placement_get(original_id)["artifact_id"] == source.artifact_id
    assert (tmp_path / "library" / "Book.epub").read_bytes() == b"book-data"
    assert any(
        (tmp_path / "library" / "Optimized.epub") == __import__("pathlib").Path(row["path"])
        for row in service.storage.placements()
        if row["artifact_id"] == derivative.artifact_id
    )


def test_multiple_variant_origins_with_one_sha_refuse_global_deletion(tmp_path):
    service, source, original_id = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=FakeTransformer())
    derived = ArtifactExecutionBrokerImpl(service).execute_transform(
        TransformRequest(source, "optimize"), control=Control(),
    )
    service.storage.artifact_variant_put({
        "variant_id": "second-source-variant",
        "artifact_id": derived.artifact_id,
        "source_artifact_id": "sha256:" + "a" * 64,
        "source_sha256": "a" * 64,
        "transformer_plugin_id": "synthetic-other",
        "options_fingerprint": "other-origin",
    })
    with pytest.raises(Conflict, match="Variante et source non indépendantes"):
        LibraryMaintenanceBrokerImpl(service).plan(
            LibraryMutationRequest("delete_variant", artifact_id=derived.artifact_id)
        )
    assert (tmp_path / "library" / "Book.epub").read_bytes() == b"book-data"
    assert (tmp_path / "library" / "Optimized.epub").is_file()
    assert service.storage.placement_get(original_id)["artifact_id"] == source.artifact_id


def test_independent_variant_deletion_retains_original_book(tmp_path):
    service, source, original_id = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=FakeTransformer())
    derivative = ArtifactExecutionBrokerImpl(service).execute_transform(
        TransformRequest(source, "optimize"), control=Control(),
    )
    broker = LibraryMaintenanceBrokerImpl(service)
    result = broker.run(
        broker.plan(LibraryMutationRequest("delete_variant", artifact_id=derivative.artifact_id)),
        Control(),
    )
    assert result.ok
    assert service.storage.artifact_variants(artifact_id=derivative.artifact_id) == []
    assert (tmp_path / "library" / "Book.epub").read_bytes() == b"book-data"
    assert not (tmp_path / "library" / "Optimized.epub").exists()
    assert service.storage.placement_get(original_id)["artifact_id"] == source.artifact_id
