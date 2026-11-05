"""#3196 D2: exact typed SourceReference vetoes legacy Kobo confirmation.

Real broker/SQLite, synthetic provider. This cannot confer a new right or
trigger a second legacy GET on an edition handled by Core A->B.
"""
from __future__ import annotations

import pytest

from tests.test_source_materialization_confirmation import case, _prepare, _dump


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
def test_partial_abi_preview_refuses_batch_containing_typed_edition(
    case, owner, buyer, state,
):
    _typed(case, owner=owner, buyer=buyer, state=state)
    before = _dump(case)
    result = case.broker.prepare_local_materialization(case.source)
    assert not result.ok
    assert result.facts["status"] == "canonical_source_delivery_active"
    assert "confirmation_token" not in result.facts
    assert not case.downloaded
    assert case.calls == {"vault-1": 0, "vault-2": 1, "vault-3": 0}
    assert _dump(case) == before


def test_typed_source_added_between_preview_and_confirm_invalidates_token(case):
    token = _prepare(case)
    _typed(case, owner="reader-3", buyer="other-borrower")
    before = _dump(case)
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok and result.facts["status"] == "canonical_source_delivery_active"
    assert not case.downloaded
    assert _dump(case) == before
    assert case.calls == {"vault-1": 0, "vault-2": 2, "vault-3": 0}
    assert case.broker.materialize_source(case.source, token).facts[
        "status"
    ] == "confirmation_unavailable"


@pytest.mark.parametrize("other_scope", [
    {"edition": "unrelated-edition"},
    {"credential": "vault-3"},
])
def test_other_typed_source_does_not_block_legacy_confirmation(case, other_scope):
    _typed(case, **other_scope)
    token = _prepare(case)
    result = case.broker.materialize_source(case.source, token)
    assert result.ok and result.changed == 2
    assert len(case.downloaded) == 2
