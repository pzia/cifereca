"""P0 #2975: one positive-loan source inventory commits atomically.

A late provider/identity collision or malformed last row must not leave the
earlier loans persisted. No negative inventory, receipt or file mutations.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from tests.test_core_p0_2975_loan_absence_scope import _loan
from tests.test_sync_state_contract import _service


def _rows(service):
    return {
        row["loan_id"]: row
        for row in service.storage.entitlements()
        if row["entitlement_type"] == "loan"
    }


def _baseline(service):
    return {
        "rights": service.storage.entitlements(),
        "acquisitions": service.storage.acquisitions(),
        "placements": service.storage.placements(),
        "artifacts": service.storage.artifacts(),
    }


def test_positive_batch_rollback_if_last_loan_belongs_to_other_local_identity(tmp_path):
    service = _service(tmp_path)
    try:
        foreign = _loan(
            "conflict", credential="shared", account="reader", identity="alex",
        )
        service._persist_active_loans("alex", [foreign], realize_expired=False)
        before = _baseline(service)
        first = _loan(
            "fresh", credential="shared", account="reader", identity="default",
        )
        # Same provider stable loan key, but a foreign Cifereca owner.
        clashing = replace(foreign, identity_id="default",
                           title="PRIVATE MUST NOT OVERWRITE")
        with pytest.raises(ValueError, match="^loan_entitlement_scope_conflict$"):
            service._persist_active_loans(
                "default", [first, clashing], realize_expired=False,
            )
        assert _baseline(service) == before
        assert "loan-fresh" not in _rows(service)
        assert "PRIVATE MUST NOT OVERWRITE" not in json.dumps(_baseline(service))
        assert _rows(service)["loan-conflict"]["identity_id"] == "alex"
        # A corrected request remains possible after SQLite's rollback.
        outcome = service._persist_active_loans(
            "default", [first], realize_expired=False,
        )
        assert outcome == {
            "persisted": 1, "returned_realized": 0, "expired_realized": 0,
        }
        assert _rows(service)["loan-fresh"]["identity_id"] == "default"
    finally:
        service.close()


def test_positive_batch_conflict_rolls_back_existing_row_refresh(tmp_path):
    service = _service(tmp_path)
    try:
        original = _loan("existing", credential="cred", account="reader")
        foreign = _loan("conflict", credential="cred", account="reader",
                        identity="alex")
        service._persist_active_loans("default", [original], realize_expired=False)
        service._persist_active_loans("alex", [foreign], realize_expired=False)
        before = _baseline(service)
        fresh = replace(original, title="PRIVATE REFRESH MUST ROLLBACK")
        collision = replace(foreign, identity_id="default")
        with pytest.raises(ValueError, match="^loan_entitlement_scope_conflict$"):
            service._persist_active_loans(
                "default", [fresh, collision], realize_expired=False,
            )
        assert _baseline(service) == before
        assert _rows(service)[original.id]["metadata"]["title"] == original.title
    finally:
        service.close()


@pytest.mark.parametrize("field,value", [
    ("source_account_id", None),
    ("id", ""),
    ("source_credential", ""),
    ("source_provider", ""),
    ("record_id", ""),  # falls back to loan.id, still a legitimate edition key
])
def test_invalid_last_loan_cannot_leave_previous_row_inserted(
    tmp_path, field, value,
):
    service = _service(tmp_path)
    try:
        first = _loan("valid")
        bad = replace(_loan("malformed"), **{field: value})
        if field == "record_id":
            # An edition-less row is permitted when the loan id is exact.
            expected = service._persist_active_loans(
                "default", [first, bad], realize_expired=False,
            )
            assert expected["persisted"] == 2
        else:
            with pytest.raises(ValueError, match="^invalid_positive_loan$"):
                service._persist_active_loans(
                    "default", [first, bad], realize_expired=False,
                )
            assert _baseline(service)["rights"] == []
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
    finally:
        service.close()


def test_successful_multi_account_batch_preserves_idempotence_and_positive_only(tmp_path):
    service = _service(tmp_path)
    try:
        primary = _loan("primary", account="primary")
        paired = _loan("paired", account="paired")
        physical = replace(_loan("paper"), medium="physical")
        first = service._persist_active_loans(
            "default", [primary, physical, paired], realize_expired=False,
        )
        assert first == {
            "persisted": 2, "returned_realized": 0, "expired_realized": 0,
        }
        assert set(_rows(service)) == {primary.id, paired.id}
        before = _baseline(service)
        second = service._persist_active_loans(
            "default", [primary, paired], realize_expired=False,
        )
        assert second == first
        assert set(_rows(service)) == set(row["loan_id"] for row in before["rights"])
        assert len(service.storage.entitlements()) == 2
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
        assert service._persist_active_loans(
            "default", [], realize_expired=False,
        ) == {
            "persisted": 0, "returned_realized": 0, "expired_realized": 0,
        }
        assert len(service.storage.entitlements()) == 2
    finally:
        service.close()
