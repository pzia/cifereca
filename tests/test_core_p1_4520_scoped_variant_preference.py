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


def _preference_request(service, derived, owner, purpose="read"):
    rows = [row for row in service.storage.placements()
            if row["artifact_id"] == derived.artifact_id and row["identity_id"] == owner]
    assert len(rows) == 1
    return LibraryMutationRequest(
        "set_preferred_variant", artifact_id=derived.artifact_id,
        target_identity_id=owner, placement_id=rows[0]["placement_id"], purpose=purpose,
    )


def test_explicit_preference_two_headless_clients_no_navigation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace

    service, source, (pid_a, pid_b), _, execution = _two_profiles(tmp_path)
    try:
        a = execution.execute_transform(_request(source, "id1", pid_a), control=Control())
        service.current_identity = NS(identity_id="id2")
        b = execution.execute_transform(_request(source, "id2", pid_b), control=Control())
        assert a.artifact_id == b.artifact_id

        class Headless:
            def __getattr__(self, name):
                if name == "current_identity":
                    pytest.fail("explicit preference consulted selector")
                return getattr(service, name)

        # Separate client instances, same durable Core storage and same SHA.
        first = LibraryMaintenanceBrokerImpl(Headless())
        second = LibraryMaintenanceBrokerImpl(Headless())
        ra, rb = (_preference_request(service, a, "id1"),
                  _preference_request(service, b, "id2", "export"))
        with pytest.raises(Conflict):
            first.admit_preferred_variant(ra)
        with pytest.raises(Conflict):
            first.admit_preferred_variant(replace(ra, placement_id=rb.placement_id),
                                          user_confirmed=True)
        with pytest.raises(Conflict):
            first.admit_preferred_variant(replace(ra, target_identity_id=None),
                                          user_confirmed=True)

        op_a = first.admit_preferred_variant(ra, user_confirmed=True)
        op_b = second.admit_preferred_variant(rb, user_confirmed=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            fa, fb = pool.submit(op_a, Control()), pool.submit(op_b, Control())
            assert fa.result().facts["identity_id"] == "id1"
            assert fb.result().facts["identity_id"] == "id2"
        with pytest.raises(Conflict):
            op_a(Control())
        assert service.storage.metadata_get(
            f"artifact.variant.preferred.id1.{source.artifact_id}.read"
        ) == a.artifact_id
        assert service.storage.metadata_get(
            f"artifact.variant.preferred.id2.{source.artifact_id}.export"
        ) == b.artifact_id
        assert service.storage.metadata_get(
            f"artifact.variant.preferred.id2.{source.artifact_id}.read"
        ) is None
    finally:
        service.storage.close()


@pytest.mark.parametrize("change", ["navigation", "epoch", "placement", "metadata"])
def test_admitted_variant_preference_checks_real_changes_not_navigation(tmp_path, change):
    service, source, (pid_a, _), _, execution = _two_profiles(tmp_path)
    service._source_context_epoch = 4
    try:
        derived = execution.execute_transform(_request(source, "id1", pid_a), control=Control())
        maintenance = LibraryMaintenanceBrokerImpl(service)
        request = _preference_request(service, derived, "id1")
        key = f"artifact.variant.preferred.id1.{source.artifact_id}.read"
        op = maintenance.admit_preferred_variant(request, user_confirmed=True)
        service.current_identity = NS(identity_id="id2")
        if change == "epoch":
            service._source_context_epoch += 1
        elif change == "placement":
            row = service.storage.placement_get(request.placement_id)
            service.storage.placement_put({**row, "state": "missing"})
        elif change == "metadata":
            service.storage.metadata_set(key, "other-confirmed-value")
        if change == "navigation":
            assert op(Control()).ok
            assert service.storage.metadata_get(key) == derived.artifact_id
        else:
            with pytest.raises(Conflict):
                op(Control())
            assert service.storage.metadata_get(key) == (
                "other-confirmed-value" if change == "metadata" else None
            )
        with pytest.raises(Conflict):
            op(Control())
        assert service.storage.metadata_get(
            f"artifact.variant.preferred.id2.{source.artifact_id}.read"
        ) is None
    finally:
        service.storage.close()


def test_legacy_preference_preview_pins_owner_and_no_headless_implicit_read(tmp_path):
    service, source, (pid_a, _), _, execution = _two_profiles(tmp_path)
    try:
        derived = execution.execute_transform(_request(source, "id1", pid_a), control=Control())
        maintenance = LibraryMaintenanceBrokerImpl(service)
        request = LibraryMutationRequest("set_preferred_variant", artifact_id=derived.artifact_id)
        plan = maintenance.plan(request)
        assert plan.request.target_identity_id == "id1"
        assert plan.request.placement_id
        service.current_identity = NS(identity_id="id2")
        with pytest.raises(Conflict):
            maintenance.run(plan, Control())
        assert service.storage.metadata_get(
            f"artifact.variant.preferred.id1.{source.artifact_id}.read"
        ) is None
        service.current_identity = None
        read = LibraryReadBrokerImpl(service)
        assert read.preferred_artifact("book-1") is None
        assert read.preferred_artifact("book-1", identity_id="id1") is not None
    finally:
        service.storage.close()


def test_reader_preference_selects_existing_exact_profile_variant_only(tmp_path):
    service, source, (pid_a, pid_b), transformer, execution = _two_profiles(tmp_path)
    db = service.storage
    try:
        # Use the existing Core publication path to produce an exact
        # derived OriginLink, then a different format declaration in this
        # SQLite-only selection fixture. This is not a content/transfer test.
        derived = execution.execute_transform(_request(source, "id1", pid_a), control=Control())
        row = db.artifact_get(derived.artifact_id)
        db.artifact_put({**row, "media_format": "kepub"})
        for owner in ("id1", "id2"):
            db.device_usb_binding_declare(identity_id=owner, device_id="reader-" + owner,
                usb_fingerprint="fixture-" + owner, label=owner, enabled=True)
            db.device_usb_reader_formats_set(identity_id=owner, device_id="reader-" + owner,
                usb_fingerprint="fixture-" + owner, supported_formats=("epub", "kepub"),
                preferred_format="kepub")
        def select(owner, pid):
            return db.device_usb_send_choice(identity_id=owner, device_id="reader-" + owner,
                                             source_placement_id=pid)
        a = select("id1", pid_a)
        b = select("id2", pid_b)
        assert a[0] == derived.artifact_id and a[2:] == ("kepub", None)
        assert b == (source.artifact_id, pid_b, "epub", None)
        assert db.device_placements() == []
        db.device_usb_intent_set(identity_id="id1", device_id="reader-id1",
            artifact_id=a[0], desired_state="keep", source_placement_id=pid_a,
            expected_send_choice=a)
        target = db.device_usb_reserve_pending_target(identity_id="id1", device_id="reader-id1",
                                                       artifact_id=a[0])
        assert target.endswith(".kepub.epub")
        old = db.device_placement_get("reader-id1", a[0])
        db.device_usb_reader_formats_set(identity_id="id1", device_id="reader-id1",
            usb_fingerprint="fixture-id1", supported_formats=("epub", "kepub"), preferred_format="epub")
        assert select("id1", pid_a) == (*a[:3], "keep")
        assert db.device_placement_get("reader-id1", a[0]) == old
        assert db.device_placement_get("reader-id1", source.artifact_id) is None
        assert db.device_placements(device_id="reader-id2") == []
        assert transformer.transform_calls == 1
        with pytest.raises(ValueError):
            db.device_usb_intent_set(identity_id="id1", device_id="reader-id1",
                artifact_id=a[0], desired_state="keep", source_placement_id=pid_a,
                expected_send_choice=a)
        assert Path(db.placement_get(pid_a)["path"]).read_bytes() == b"book-data"
        assert Path(db.placement_get(pid_b)["path"]).read_bytes() == b"book-data"
    finally:
        db.close()
