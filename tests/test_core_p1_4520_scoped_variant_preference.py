"""#4520: derivative preference is local to the owning identity.

Synthetic filesystem + SQLite. No provider/Adobe network or entitlement
operations. A global SHA recipe and a manual copy are not derived provenance.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import hashlib
from pathlib import Path

import pytest

from cifereca.artifact_runtime import (
    ArtifactExecutionBrokerImpl, LibraryReadBrokerImpl,
    LibraryMaintenanceBrokerImpl,
)
from cifereca.plugin_api import Conflict, LibraryMutationRequest, TransformRequest
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)
from tests.test_core_p1_4520_exact_placement_transform import _two_profiles


def _request(source, owner, placement_id):
    return TransformRequest(
        source, "optimize", identity_id=owner, placement_id=placement_id,
    )


def _prefer(maintenance, aid, *, purpose="read", owner=None):
    request = LibraryMutationRequest(
        "set_preferred_variant", artifact_id=aid, purpose=purpose,
        target_identity_id=owner,
    )
    return maintenance.run(maintenance.plan(request), Control())


def test_same_sha_two_profiles_preferences_are_separate_and_read_scoped(tmp_path):
    service, source, (pid_a, pid_b), transformer, execution = _two_profiles(tmp_path)
    maintenance = LibraryMaintenanceBrokerImpl(service)
    read = LibraryReadBrokerImpl(service)
    try:
        derived_a = execution.execute_transform(
            _request(source, "id1", pid_a), control=Control(),
        )
        _prefer(maintenance, derived_a.artifact_id, owner="id1")
        assert read.preferred_artifact("book-1", identity_id="id1").artifact_id == (
            derived_a.artifact_id
        )
        # B owns the same global source SHA but not an output Placement yet.
        assert read.preferred_artifact("book-1", identity_id="id2").artifact_id == (
            source.artifact_id
        )

        service.current_identity = NS(identity_id="id2")
        derived_b = execution.execute_transform(
            _request(source, "id2", pid_b), control=Control(),
        )
        assert derived_b.artifact_id == derived_a.artifact_id
        # B has a child Placement, yet A's read preference must not apply.
        assert read.preferred_artifact("book-1", identity_id="id2").artifact_id == (
            source.artifact_id
        )
        _prefer(maintenance, derived_b.artifact_id, purpose="export", owner="id2")
        assert read.preferred_artifact(
            "book-1", purpose="export", identity_id="id2"
        ).artifact_id == derived_b.artifact_id
        assert read.preferred_artifact(
            "book-1", purpose="export", identity_id="id1"
        ).artifact_id == source.artifact_id

        scoped_a = f"artifact.variant.preferred.id1.{source.artifact_id}.read"
        scoped_b = f"artifact.variant.preferred.id2.{source.artifact_id}.export"
        assert service.storage.metadata_get(scoped_a) == derived_a.artifact_id
        assert service.storage.metadata_get(scoped_b) == derived_b.artifact_id
        assert service.storage.metadata_get(
            f"artifact.variant.preferred.{source.artifact_id}.read"
        ) is None
        assert transformer.transform_calls == 2
    finally:
        service.storage.close()


def test_cross_profile_preference_target_refused_before_mutating_metadata(tmp_path):
    service, source, (pid_a, _), _, execution = _two_profiles(tmp_path)
    maintenance = LibraryMaintenanceBrokerImpl(service)
    try:
        derived = execution.execute_transform(
            _request(source, "id1", pid_a), control=Control(),
        )
        service.current_identity = NS(identity_id="id2")
        with pytest.raises(Conflict):
            _prefer(maintenance, derived.artifact_id, owner="id1")
        with pytest.raises(Conflict):
            _prefer(maintenance, derived.artifact_id, owner="id2")
        assert service.storage.metadata_get(
            f"artifact.variant.preferred.id1.{source.artifact_id}.read"
        ) is None
    finally:
        service.storage.close()


def test_stale_plan_is_rechecked_after_variant_materialization_disappears(tmp_path):
    service, source, _pid = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=FakeTransformer())
    execution = ArtifactExecutionBrokerImpl(service)
    maintenance = LibraryMaintenanceBrokerImpl(service)
    derived = execution.execute_transform(
        TransformRequest(source, "optimize"), control=Control(),
    )
    request = LibraryMutationRequest(
        "set_preferred_variant", artifact_id=derived.artifact_id,
    )
    plan = maintenance.plan(request)
    physical = next(
        p for p in service.storage.placements()
        if p["artifact_id"] == derived.artifact_id
    )
    # An old plan cannot reselect missing bytes or mutate a different
    # identity's preference. No actual unlink is needed for this CAS check.
    service.storage.placement_put({**physical, "state": "missing"})
    with pytest.raises(Conflict):
        maintenance.run(plan, Control())
    assert service.storage.metadata_get(
        f"artifact.variant.preferred.id1.{source.artifact_id}.read"
    ) is None


def test_unqualified_legacy_recipe_and_manual_sha_copy_never_grant_preference(tmp_path):
    service, source, source_pid = make_service(tmp_path)
    copied = tmp_path / "library" / "Unrelated.epub"
    copied.write_bytes(b"unrelated-optimized")
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    aid = "sha256:" + digest
    service.storage.artifact_put({
        "artifact_id": aid, "sha256": digest, "size": copied.stat().st_size,
        "media_format": "epub", "kind": "file", "metadata": {},
    })
    service.storage.placement_put({
        "artifact_id": aid, "path": str(copied),
        "identity_id": "id1", "provider_id": "manual",
        "source_item_id": "book-1", "state": "present", "metadata": {},
    })
    service.storage.artifact_variant_put({
        "artifact_id": aid, "source_artifact_id": source.artifact_id,
        "source_sha256": source.sha256,
        "transformer_plugin_id": "document.transformer.test",
        "variant_kind": "optimized", "metadata": {},
    })
    maintenance = LibraryMaintenanceBrokerImpl(service)
    read = LibraryReadBrokerImpl(service)
    try:
        with pytest.raises(Conflict):
            _prefer(maintenance, aid, owner="id1")
        global_key = f"artifact.variant.preferred.{source.artifact_id}.read"
        service.storage.metadata_set(global_key, aid)
        assert read.preferred_artifact("book-1", identity_id="id1").artifact_id == (
            source.artifact_id
        )
        assert service.storage.metadata_get(global_key) == aid
        assert service.storage.placement_get(source_pid)["artifact_id"] == source.artifact_id
    finally:
        service.storage.close()
