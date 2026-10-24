"""P0 #2797: declared history remains read-only after supplier changes.

A statement is local provenance, not a receipt, provider right or permanent
library loan. Its owner may examine a stale/revoked relation even if the
original account, entitlement, file or connection has disappeared.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.core import library_declared_links as engine
from tests.test_library_declared_links import (
    apply, call, inspect, records, rig, rows,
)


_EVIDENCE = (
    "entitlements", "acquisition_ledger", "artifacts",
    "placements", "catalog_entries", "provider_connections",
)


def _original_evidence(service):
    return {table: rows(service, table) for table in _EVIDENCE}


@pytest.mark.parametrize("change", ["account_removed", "account_reassigned",
                                      "right_deleted", "connection_deleted"])
def test_historical_declaration_needs_review_when_supplier_disappears(rig, change):
    service, _book, _root, _right, _options = rig
    created = apply(rig)
    assert inspect(rig, created.relation_id).status == "declared_current"
    if change == "account_removed":
        service.accounts.clear()
    elif change == "account_reassigned":
        service.accounts[0].identity_id = "alex"
    else:
        with service.storage._lock, service.storage._connect() as db:
            if change == "right_deleted":
                db.execute("DELETE FROM entitlements")
            else:
                db.execute("DELETE FROM provider_connections")
    before = _original_evidence(service)
    statement = records(service)
    result = inspect(rig, created.relation_id)
    assert result.status == "needs_review"
    assert result.trust == "user_declaration"
    assert result.changed == 0
    assert result.rights_mutations == result.file_mutations == result.placement_mutations == 0
    assert result.before == statement[0]
    history = engine.list_declared_links(service, identity_id="default")
    assert history.total == 1
    assert history.rows[0].review == "source_or_right_changed"
    assert _original_evidence(service) == before
    assert records(service) == statement
    assert not rows(service, "acquisition_ledger")
    assert not rows(service, "placements")
    assert "PRIVATE" not in repr(result)
    assert "PRIVATE" not in repr(history)
    # A stale source may be consulted, but NOT used to create another link.
    with pytest.raises(engine.DeclarationRejected):
        call(rig)


@pytest.mark.parametrize("change", ["account_removed", "right_deleted",
                                      "connection_deleted", "file_deleted"])
def test_revoked_history_requires_no_live_supplier_or_file(rig, change, monkeypatch):
    service, book, _root, _right, _options = rig
    created = apply(rig)
    plan = engine.withdraw_declared_link(
        service, identity_id="default", relation_id=created.relation_id,
    )
    assert plan.status == "revoke_preview"
    revoked = engine.withdraw_declared_link(
        service, identity_id="default", relation_id=created.relation_id,
        confirmation=plan.plan_token,
    )
    assert revoked.status == "revoked"
    if change == "account_removed":
        service.accounts.clear()
    elif change == "file_deleted":
        book.unlink()
    else:
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM " + (
                "entitlements" if change == "right_deleted"
                else "provider_connections"
            ))
    before = _original_evidence(service)
    statement = records(service)
    # A revoked historical record must not ask the file system or the source
    # to prove anything new. Revocation carries no attachment authority.
    def should_not_touch_file(*_args, **_kwargs):
        raise AssertionError("revoked history attempted file revalidation")
    monkeypatch.setattr(engine, "revalidate_observed_file", should_not_touch_file)
    result = inspect(rig, created.relation_id)
    assert result.status == "revoked"
    assert result.before == statement[0]
    assert result.changed == 0
    assert _original_evidence(service) == before
    assert records(service) == statement
    assert engine.list_declared_links(
        service, identity_id="default", include_revoked=True,
    ).rows[0].review == "revoked"
    assert "PRIVATE" not in repr(result)


def test_stale_history_cannot_cross_identity_or_mismatch_source(rig):
    service, book, _root, _right, _options = rig
    created = apply(rig)
    service.accounts.clear()
    with pytest.raises(engine.DeclarationRejected, match="identity_not_active"):
        inspect(rig, created.relation_id, identity_id="alex")
    with pytest.raises(engine.DeclarationRejected, match="relation_scope_changed"):
        inspect(rig, created.relation_id, account_id="other-account")
    with pytest.raises(engine.DeclarationRejected, match="file_scope_changed"):
        inspect(rig, created.relation_id, observation=replace(
            rig[4]["observation"], identity_id="alex",
        ))
    assert len(records(service)) == 1
    assert book.exists()


def test_malformed_declaration_namespace_still_fails_closed_for_history(rig):
    service, _book, _root, _right, _options = rig
    created = apply(rig)
    service.accounts.clear()
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (engine._PREFIX + "a" * 32, "{PRIVATE_corrupt_json"),
        )
    with pytest.raises(engine.DeclarationRejected, match="invalid_local_context"):
        inspect(rig, created.relation_id)
    assert not rows(service, "placements")
    assert "PRIVATE" not in repr(created)
