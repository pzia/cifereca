"""P0 #2975: an observed positive loan without a new due date is NOT expired.

The old SQLite due date is historical, not an attested deadline for a new
positive supplier observation. This is not a loan extension or ownership
assertion; expiration remains unknown until a source proves a new date.
"""
from __future__ import annotations

from dataclasses import replace
import json

from cifereca.core import library_mutation
from tests.test_core_p0_2975_loan_absence_scope import _loan
from tests.test_sync_state_contract import _service


def _persist(service, rows, *, expired=True):
    return library_mutation._persist_active_loans(
        service, "default", rows, realize_expired=expired,
    )


def _loan_rows(service):
    return {r["loan_id"]: r for r in service.storage.entitlements()
            if r["entitlement_type"] == "loan"}


def test_positive_refresh_with_unknown_due_does_not_restore_stale_expiration(tmp_path):
    service = _service(tmp_path)
    try:
        original = replace(
            _loan("old"), due_at="2001-01-01T00:00:00Z",
        )
        _persist(service, [original], expired=False)
        before = _loan_rows(service)[original.id]
        assert before["expires_at"] == original.due_at
        assert before["state"] == "active"

        # Provider still lists the loan NOW, but no longer provides a date.
        current = replace(original, due_at=None, title="Current positive observation")
        outcome = _persist(service, [current], expired=True)
        refreshed = _loan_rows(service)[original.id]
        assert outcome == {
            "persisted": 1, "returned_realized": 0, "expired_realized": 0,
        }
        assert refreshed["entitlement_id"] == before["entitlement_id"]
        assert refreshed["state"] == "active"
        assert refreshed["expires_at"] is None
        assert refreshed["metadata"]["title"] == current.title
        assert refreshed["entitlement_type"] == "loan"
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
    finally:
        service.close()


def test_unknown_due_does_not_extend_an_other_accounts_stated_deadline(tmp_path):
    service = _service(tmp_path)
    try:
        old_a = replace(_loan("A", account="borrower-A"),
                        due_at="2001-01-01T00:00:00Z")
        other_b = replace(_loan("B", account="borrower-B"),
                          due_at="2099-01-01T00:00:00Z")
        _persist(service, [old_a, other_b], expired=False)
        _persist(service, [replace(old_a, due_at=None)], expired=False)
        rows = _loan_rows(service)
        assert rows[old_a.id]["expires_at"] is None
        assert rows[old_a.id]["state"] == "active"
        assert rows[other_b.id]["expires_at"] == other_b.due_at
        assert rows[other_b.id]["state"] == "active"
    finally:
        service.close()


def test_confirmed_new_due_date_replaces_old_date_and_is_still_expirable(tmp_path):
    service = _service(tmp_path)
    try:
        earlier = replace(_loan("renew"), due_at="2001-01-01T00:00:00Z")
        _persist(service, [earlier], expired=False)
        future = replace(earlier, due_at="2099-01-01T00:00:00Z")
        outcome = _persist(service, [future], expired=True)
        assert outcome["expired_realized"] == 0
        assert _loan_rows(service)[earlier.id]["expires_at"] == future.due_at
        assert service.storage.entitlement_realize_expired(
            now=4102444800,  # 2100-01-01 UTC
        ) == 1
        assert _loan_rows(service)[earlier.id]["state"] == "realized"
        assert not service.storage.placements()
    finally:
        service.close()


def test_unobserved_loan_keeps_original_due_and_is_never_cleared_by_empty_sync(tmp_path):
    service = _service(tmp_path)
    try:
        old = replace(_loan("still-unknown"), due_at="2001-01-01T00:00:00Z")
        _persist(service, [old], expired=False)
        before = _loan_rows(service)
        result = _persist(service, [], expired=False)
        assert result == {
            "persisted": 0, "returned_realized": 0, "expired_realized": 0,
        }
        assert _loan_rows(service) == before
        assert "PRIVATE" not in json.dumps(result)
    finally:
        service.close()
