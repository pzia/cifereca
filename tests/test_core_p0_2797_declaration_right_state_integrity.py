"""P0 #2797: corrupt private declarations must not masquerade as current rights."""
from __future__ import annotations

import json

import pytest

from cifereca.core import library_declared_links as engine
from tests.test_library_declared_links import apply, call, inspect, rig


@pytest.mark.parametrize("field,value", [
    ("entitlement_id", None),
    ("entitlement_id", ""),
    ("entitlement_type", {"private": "object"}),
    ("state", ["active"]),
    ("loan_id", 123),
    ("expires_at", float("nan")),
    ("expires_at", "PRIVATE" * 300),
])
def test_malformed_right_snapshot_refused_by_preview_history_and_inspection(
    rig, field, value,
):
    service = rig[0]
    declared = apply(rig)
    with service.storage._lock, service.storage._connect() as db:
        key = engine._PREFIX + declared.relation_id
        row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        assert row is not None
        record = json.loads(row[0])
        record["right_state"][field] = value
        db.execute("UPDATE metadata SET value=? WHERE key=?", (
            json.dumps(record), key,
        ))
    before = service.storage.entitlements()
    for operation in (
        lambda: call(rig),
        lambda: inspect(rig, declared.relation_id),
        lambda: engine.list_declared_links(service, identity_id="default"),
        lambda: engine.withdraw_declared_link(
            service, identity_id="default", relation_id=declared.relation_id,
        ),
    ):
        with pytest.raises(engine.DeclarationRejected) as exc:
            operation()
        assert str(exc.value) in {"declaration_store_invalid", "invalid_local_context"}
        assert "PRIVATE" not in str(exc.value)
    assert service.storage.entitlements() == before
    assert service.storage.acquisitions() == []
    assert service.storage.placements() == []


def test_valid_owned_and_loan_right_state_remain_readable(rig):
    service = rig[0]
    declared = apply(rig)
    assert engine.list_declared_links(
        service, identity_id="default",
    ).total == 1
    assert inspect(rig, declared.relation_id).status == "declared_current"
