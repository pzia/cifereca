"""#3196 D2: typed SourceReference cannot authorize retired Kobo W2.

The 0.7 confirmation path is retired even without an existing typed source.
Keep independent exact-source guards and the proof that no legacy provider
manifest/GET or backup_items writer is entered.
"""
from __future__ import annotations

import pytest

from tests.test_source_materialization_confirmation import case, _dump


def _typed(case, *, edition="PRIVATE-book-1", credential="vault-2",
           owner="reader-2", buyer="PRIVATE-account-2", state="active"):
    with case.service.storage._lock, case.service.storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("source:typed", owner, "kobo", credential, buyer,
             "purchases", edition, "purchase", "purchase", state, 1, 1),
        )


@pytest.mark.parametrize("owner,buyer,state", [
    ("reader-2", "PRIVATE-account-2", "active"),
    ("reader-2", "other-borrower", "unknown"),
    ("reader-3", "other-borrower", "inactive"),
])
def test_typed_source_cannot_revive_w2_confirmation(case, owner, buyer, state):
    _typed(case, owner=owner, buyer=buyer, state=state)
    before = _dump(case)
    result = case.broker.prepare_local_materialization(case.source)
    assert not result.ok
    assert result.facts["status"] in {
        "canonical_source_delivery_active", "canonical_delivery_unavailable",
    }
    assert "confirmation_token" not in result.facts
    assert not case.downloaded
    assert not any(case.calls.values())
    assert _dump(case) == before


@pytest.mark.parametrize("other_scope", [
    {"edition": "unrelated-edition"},
    {"credential": "vault-3"},
])
def test_unrelated_typed_source_grants_no_w2_authority(case, other_scope):
    _typed(case, **other_scope)
    before = _dump(case)
    prepared = case.broker.prepare_local_materialization(case.source)
    assert not prepared.ok
    assert prepared.facts["status"] == "canonical_delivery_unavailable"
    assert "confirmation_token" not in prepared.facts
    executed = case.broker.materialize_source(case.source, "a" * 64)
    assert not executed.ok
    assert executed.facts["status"] == "canonical_delivery_unavailable"
    assert not case.downloaded
    assert not any(case.calls.values())
    assert _dump(case) == before
