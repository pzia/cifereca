"""P0 #2847: Core-only inspection and one-use consent for linked SQL retirement.

A linked catalogue cannot be detached by the ordinary orphan button.
Only the separate reviewed route, scoped to a fully observed path, can
detach the local projection. No GTK action is added by this PR.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from cifereca.core.missing_placement_operator import MissingPlacementOperatorSession
from tests.test_core_p0_2847_operator_session import _session
from tests.test_core_p0_2847_relocation_transaction import catalogue, rig


def test_explicit_operator_linked_preview_keeps_token_private_and_is_one_use(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    _service, operator = _session(rig)
    assert operator.preview_retirement(placement_id=pid)["status"] == "blocked"
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    preview = operator.preview_linked_catalogue_retirement(placement_id=pid)
    assert preview == {
        "status": "review_required",
        "reason": "catalogue_detachment_requires_confirmation",
        "catalogue_rows": 1,
        "requires_explicit_confirmation": True,
        "read_only": True,
    }
    assert str(old) not in str(preview)
    assert pid not in str(preview)
    assert "plan_token" not in str(preview)
    assert operator.confirm()["reason"] == "confirmation_required"
    assert db.placement_get(pid)["state"] == "missing"
    assert operator.preview_linked_catalogue_retirement(placement_id=pid)[
        "status"
    ] == "review_required"
    committed = operator.confirm(user_confirmed=True)
    assert committed["status"] == "retired"
    assert committed["reason"] == "catalogue_detached"
    assert db.catalog_entry_get("catalogue:original")["placement_id"] is None
    assert db.placement_get(pid) is None
    assert new.read_bytes() == payload
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"


def test_linked_plan_revoked_by_identity_switch_and_checkpoint(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    service, operator = _session(rig)
    assert operator.preview_linked_catalogue_retirement(placement_id=pid)[
        "status"
    ] == "review_required"
    service.current_identity.identity_id = "bob"
    assert operator.confirm(user_confirmed=True)["reason"] == "scope_changed"
    assert db.placement_get(pid)["state"] == "missing"
    service.current_identity.identity_id = "alice"
    assert operator.preview_linked_catalogue_retirement(placement_id=pid)[
        "status"
    ] == "review_required"
    db.metadata_set("checkpoint.opaque", json.dumps({"placement_id": pid}))
    assert operator.confirm(user_confirmed=True)["status"] == "blocked"
    assert db.placement_get(pid)["state"] == "missing"
    assert db.catalog_entry_get("catalogue:original")["placement_id"] == pid
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"


def test_incomplete_operator_can_never_prepare_linked_detachment(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    service, _operator = _session(rig)
    incomplete = MissingPlacementOperatorSession(
        service, identity_id="alice", scope_valid=lambda: True,
        inspection_complete=False,
    )
    assert incomplete.preview_linked_catalogue_retirement(placement_id=pid) == {
        "status": "blocked", "reason": "incomplete_inspection",
        "read_only": True,
    }
    assert incomplete.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert db.placement_get(pid)["state"] == "missing"


def test_real_scoped_inspection_requires_observed_path_key_and_second_consent(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    service, _operator = _session(rig)
    audit = audit_library(db.path, {"alice": (root / "alice",)})
    inspected = LocalInspection(
        audit, lambda: True, "alice", roots=(root / "alice",), service=service,
    )
    try:
        assert inspected._manual_audit_complete
        key = next(
            k for k, value in inspected._missing_case_placements.items()
            if value == pid
        )
        assert inspected.preview_missing_linked_retirement(
            "path:unobserved",
        )["status"] == "blocked"
        preview = inspected.preview_missing_linked_retirement(key)
        assert preview["status"] == "review_required"
        assert preview["catalogue_rows"] == 1
        assert "plan_token" not in preview
        assert inspected.confirm_missing_repair(
            "path:unobserved", user_confirmed=True,
        )["reason"] == "confirmation_required"
        assert db.placement_get(pid)["state"] == "missing"
        assert inspected.preview_missing_linked_retirement(key)[
            "status"
        ] == "review_required"
        assert inspected.confirm_missing_repair(
            key, user_confirmed=True,
        )["status"] == "retired"
        assert db.placement_get(pid) is None
        assert db.catalog_entry_get("catalogue:original")["placement_id"] is None
        assert not inspected.valid()
    finally:
        inspected.close()
