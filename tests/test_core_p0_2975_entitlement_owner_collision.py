"""P0 #2975: SQLite entitlements are NOT movable by a normal sync UPSERT.

The historical stable entitlement key omits the Cifereca identity. An account
rebound or colliding manual source key must fail atomically and preserve the
earlier owner, right status, origin and acquisition history.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.storage import Storage
from cifereca.models import Loan
from tests.test_sync_state_contract import _service


PRIVATE = "PRIVATE_BORROWER_DO_NOT_DISCLOSE"


def _row(*, identity="default", provider="paris", credential="cred",
         account="primary", item="edition", loan_id="loan", state="active"):
    return {
        "identity_id": identity, "provider_id": provider,
        "credential_id": credential, "account_id": account,
        "item_id": item, "loan_id": loan_id,
        "entitlement_type": "loan", "state": state,
        "metadata": {"title": "PRIVATE title"},
    }


def _rights(storage):
    return storage.entitlements(provider_id="paris")


def test_same_source_refresh_is_idempotent_but_other_identity_cannot_steal_right(
    tmp_path,
):
    storage = Storage(tmp_path / "PRIVATE.sqlite")
    try:
        row = _row()
        eid = storage.entitlement_put(row)
        first = _rights(storage)[0]
        assert eid == first["entitlement_id"]
        assert storage.entitlement_put({**row, "metadata": {"title": "PRIVATE updated"}}) == eid
        before = _rights(storage)
        with pytest.raises(ValueError, match="^entitlement_source_scope_conflict$"):
            storage.entitlement_put({
                **row, "identity_id": "alex",
                "state": "realized",
                "metadata": {"title": PRIVATE},
            })
        assert _rights(storage) == before
        assert _rights(storage)[0]["identity_id"] == "default"
        assert _rights(storage)[0]["state"] == "active"
        assert PRIVATE not in json.dumps({"code": "entitlement_source_scope_conflict"})
    finally:
        storage.close()


@pytest.mark.parametrize("field,replacement", [
    ("provider_id", "kobo"), ("credential_id", "other"),
    ("account_id", "other"), ("item_id", "other"),
    ("loan_id", "another-loan"), ("entitlement_type", "owned"),
])
def test_explicit_id_cannot_be_used_to_relabel_a_different_source(
    tmp_path, field, replacement,
):
    storage = Storage(tmp_path / "PRIVATE.sqlite")
    try:
        row = _row()
        eid = storage.entitlement_put(row)
        before = _rights(storage)
        with pytest.raises(ValueError, match="^entitlement_source_scope_conflict$"):
            storage.entitlement_put({
                **row, field: replacement, "entitlement_id": eid,
                "state": "realized", "metadata": {"title": PRIVATE},
            })
        assert _rights(storage) == before
        assert not storage.acquisitions()
        assert not storage.placements()
    finally:
        storage.close()


def test_legacy_null_identity_can_be_claimed_once_not_reassigned(
    tmp_path,
):
    storage = Storage(tmp_path / "PRIVATE.sqlite")
    try:
        row = _row(identity=None)
        eid = storage.entitlement_put(row)
        assert storage.entitlement_put({**row, "identity_id": "default"}) == eid
        assert _rights(storage)[0]["identity_id"] == "default"
        with pytest.raises(ValueError, match="^entitlement_source_scope_conflict$"):
            storage.entitlement_put({**row, "identity_id": "alex"})
        with pytest.raises(ValueError, match="^entitlement_source_scope_conflict$"):
            storage.entitlement_put({**row, "identity_id": None})
        assert _rights(storage)[0]["identity_id"] == "default"
    finally:
        storage.close()


def test_two_actual_identity_loan_syncs_cannot_rewrite_existing_owner_or_history(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        loan = Loan(
            id="loan", provider="DILICOM", medium="digital",
            record_id="edition", title="PRIVATE title",
            source_provider="paris", source_credential="cred",
            source_account_id="primary", identity_id="default",
            due_at="2099-01-01T00:00:00Z",
        )
        assert service._persist_active_loans("default", [loan])["persisted"] == 1
        right = _rights(service.storage)[0]
        service.storage.acquisition_put({
            "acquisition_id": "acq-owned",
            "identity_id": "default", "provider_id": "paris",
            "credential_id": "cred", "account_id": "primary",
            "item_id": "edition", "entitlement_id": right["entitlement_id"],
            "status": "complete",
        })
        rights_before = _rights(service.storage)
        ledger_before = service.storage.acquisitions()
        foreign = replace(loan, identity_id="alex", title=PRIVATE)
        with pytest.raises(ValueError, match="^entitlement_source_scope_conflict$"):
            service._persist_active_loans("alex", [foreign])
        assert _rights(service.storage) == rights_before
        assert service.storage.acquisitions() == ledger_before
        assert _rights(service.storage)[0]["identity_id"] == "default"
        assert not service.storage.placements()
    finally:
        service.close()
