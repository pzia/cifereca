"""P0 #2797: quota pressure must never strand a reversible user declaration.

All records and EPUB bytes are local synthetic fixtures. The low quota is
injected only after a valid declaration has been durably recorded.
"""
from __future__ import annotations

import json
import time

import pytest

from cifereca.core import library_declared_links as engine
from cifereca.core.library_declared_links import DeclarationRejected
from tests.test_library_declared_links import apply, call, records, rig, rows


def test_legacy_declaration_withdrawal_replaces_instead_of_doubling_quota(
    rig, monkeypatch,
):
    service, book, _root, _right, _options = rig
    original_file = book.read_bytes()
    declaration = apply(rig)
    before = records(service)
    assert len(before) == 1
    assert before[0]["state"] == "active"
    stored_size = len(engine._json(before[0]))
    replacement_size = len(engine._json({
        **before[0], "state": "revoked", "revoked_at": int(time.time()),
    }))
    # Old and new each fit; their sum DOES NOT. This is a replacement,
    # not an additional declaration or a second right/placement.
    quota = max(stored_size, replacement_size) + 8
    assert stored_size + replacement_size > quota
    monkeypatch.setattr(engine, "_MAX_STORE_BYTES", quota)
    content_before = {
        table: rows(service, table)
        for table in ("entitlements", "acquisition_ledger", "placements",
                      "artifacts", "provider_connections")
    }
    preview = call(rig, revoke=declaration.relation_id)
    assert preview.status == "revoke_preview"
    result = call(
        rig, revoke=declaration.relation_id,
        confirmation=preview.plan_token,
    )
    assert result.status == "revoked"
    assert result.changed == 1
    assert len(records(service)) == 1
    assert records(service)[0]["state"] == "revoked"
    assert records(service)[0]["origin"] == "user_declaration"
    assert records(service)[0]["revoked_at"] is not None
    assert len(engine._json(records(service)[0])) <= quota
    assert call(rig, revoke=declaration.relation_id).status == "already_revoked"
    assert content_before == {
        table: rows(service, table) for table in content_before
    }
    assert book.read_bytes() == original_file
    assert "PRIVATE" not in repr(result)


def test_creation_still_refused_when_store_budget_is_exhausted(rig, monkeypatch):
    service, book, _root, _right, _options = rig
    declaration = apply(rig)
    record = records(service)[0]
    monkeypatch.setattr(
        engine, "_MAX_STORE_BYTES", len(engine._json(record)) + 8,
    )
    before = rows(service, "metadata")
    # A second declaration (other right/edition) cannot silently bypass
    # the store budget just because withdrawal has replacement semantics.
    service.storage.entitlement_put({
        "entitlement_id": "another-right",
        "identity_id": "default",
        "provider_id": rig[4]["provider_id"],
        "credential_id": rig[4]["credential_id"],
        "account_id": rig[4]["account_id"],
        "item_id": "OTHER-item",
        "entitlement_type": "owned",
        "state": "active",
    })
    other = book.with_name("other.epub")
    other.write_bytes(book.read_bytes())
    from cifereca.core.library_inventory import scan_identity_roots
    observation = next(
        o for o in scan_identity_roots({"default": (rig[2],)}).files
        if o.path == str(other)
    )
    with pytest.raises(DeclarationRejected, match="^declaration_budget$"):
        preview = call(
            rig, observation=observation, item_id="OTHER-item",
        )
        call(
            rig, observation=observation, item_id="OTHER-item",
            confirmation=preview.plan_token,
        )
    assert rows(service, "metadata") == before
    assert records(service)[0]["state"] == "active"
    assert "PRIVATE" not in repr(declaration)


def test_over_quota_or_corrupted_registry_does_not_authorize_withdrawal(
    rig, monkeypatch,
):
    service = rig[0]
    declaration = apply(rig)
    record = records(service)[0]
    before = rows(service, "metadata")
    monkeypatch.setattr(
        engine, "_MAX_STORE_BYTES", len(engine._json(record)) - 1,
    )
    with pytest.raises(DeclarationRejected, match="^declaration_budget$"):
        call(rig, revoke=declaration.relation_id)
    assert rows(service, "metadata") == before
