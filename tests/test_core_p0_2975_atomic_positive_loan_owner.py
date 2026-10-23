"""P0 #2975: same provider loan key must not transfer between local identities.

The Entitlement stable ID omits Cifereca's identity. A second profile linked
to the same upstream account can observe a legitimate identical positive loan
without gaining the first profile's durable entitlement, file or acquisition.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.acquisition_engine import Entitlement, SourceIdentity
from tests.test_core_p0_2975_loan_absence_scope import _loan
from tests.test_sync_state_contract import _service


def _snapshot(service):
    return {
        name: (
            service.storage.entitlements()
            if name == "entitlements"
            else service.storage.acquisitions()
            if name == "acquisition_ledger"
            else service.storage.placements()
            if name == "placements"
            else service.storage.artifacts()
        ) for name in (
            "entitlements", "acquisition_ledger", "placements", "artifacts",
        )
    }


def test_exact_source_loan_can_be_refreshed_without_reassigning_owner(tmp_path):
    service = _service(tmp_path)
    try:
        loan = _loan("shared", account="linked-reader", credential="same-credential")
        assert service._persist_active_loans("default", [loan])["persisted"] == 1
        saved = service.storage.entitlements()[0]
        assert saved["identity_id"] == "default"
        assert service._persist_active_loans("default", [
            replace(loan, title="Updated synthetic loan"),
        ])["persisted"] == 1
        updated = service.storage.entitlements()[0]
        assert updated["identity_id"] == "default"
        assert updated["metadata"]["title"] == "Updated synthetic loan"
        assert updated["entitlement_id"] == saved["entitlement_id"]
    finally:
        service.close()


def test_foreign_identity_with_same_provider_loan_cannot_hijack_sqlite_right(tmp_path):
    service = _service(tmp_path)
    try:
        original = _loan("same", account="linked-reader", credential="shared")
        foreign = replace(original, identity_id="alex", title="OTHER SECRET TITLE")
        service._persist_active_loans("default", [original])
        before = _snapshot(service)
        with pytest.raises(ValueError, match="^loan_entitlement_scope_conflict$"):
            service._persist_active_loans("alex", [foreign])
        assert _snapshot(service) == before
        row, = service.storage.entitlements()
        assert row["identity_id"] == "default"
        assert row["state"] == "active"
        assert row["metadata"]["title"] == original.title
        assert "OTHER SECRET TITLE" not in json.dumps(_snapshot(service))
        # The original owner can still refresh independently.
        assert service._persist_active_loans("default", [original])["persisted"] == 1
    finally:
        service.close()


@pytest.mark.parametrize("changed", [
    {"identity_id": "alex"},
    {"credential_id": "changed-credential"},
    {"account_id": "foreign-account"},
    {"item_id": "changed-book"},
    {"loan_id": "changed-loan"},
])
def test_existing_entitlement_collision_checks_every_owner_coordinate(
    tmp_path, changed,
):
    service = _service(tmp_path)
    try:
        loan = _loan("one", credential="credential", account="account")
        from cifereca.acquisition_engine import Entitlement
        first = Entitlement(
            source=SourceIdentity(
                provider_id="paris", credential_id="credential",
                account_id="account", identity_id="default",
            ),
            item_id=loan.record_id, entitlement_type="loan",
            loan_id=loan.id, state="active", metadata={"title": "PRIVATE ORIGINAL"},
        )
        # A stored row with a stable PK but conflicting persisted coordinates
        # is possible after a legacy import/manual repair. Never overwrite it.
        service.storage.entitlement_put({
            "entitlement_id": first.entitlement_id,
            "identity_id": changed.get("identity_id", "default"),
            "provider_id": "paris",
            "credential_id": changed.get("credential_id", "credential"),
            "account_id": changed.get("account_id", "account"),
            "item_id": changed.get("item_id", loan.record_id),
            "entitlement_type": "loan", "state": "realized",
            "loan_id": changed.get("loan_id", loan.id),
            "metadata": {"title": "PRIVATE HISTORICAL"},
        })
        before = _snapshot(service)
        with pytest.raises(ValueError, match="^loan_entitlement_scope_conflict$"):
            service.storage.entitlement_put_positive_loan(first)
        assert _snapshot(service) == before
    finally:
        service.close()


def test_legacy_unknown_identity_never_becomes_implicit_current_ownership(tmp_path):
    service = _service(tmp_path)
    try:
        loan = _loan("legacy", credential="cred", account="reader")
        entitlement = Entitlement(
            source=SourceIdentity(
                "paris", "cred", "reader", "default",
            ),
            item_id=loan.record_id, entitlement_type="loan",
            loan_id=loan.id,
        )
        service.storage.entitlement_put({
            "entitlement_id": entitlement.entitlement_id,
            "identity_id": None, "provider_id": "paris",
            "credential_id": "cred", "account_id": "reader",
            "item_id": loan.record_id, "entitlement_type": "loan",
            "state": "active", "loan_id": loan.id,
        })
        before = _snapshot(service)
        with pytest.raises(ValueError, match="^loan_entitlement_scope_conflict$"):
            service.storage.entitlement_put_positive_loan(entitlement)
        assert _snapshot(service) == before
    finally:
        service.close()


def test_manual_positive_writer_rejects_missing_borrower_context(tmp_path):
    service = _service(tmp_path)
    try:
        loan = _loan("invalid")
        with pytest.raises(ValueError, match="^invalid_positive_loan$"):
            service._persist_active_loans("default", [
                replace(loan, source_account_id=None),
            ])
        assert _snapshot(service)["entitlements"] == []
    finally:
        service.close()
