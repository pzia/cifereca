"""P0 #1120: exact inventory checks cannot disclose provider exception types.

All return operations use synthetic sessions and local temporary SQLite.
No Adobe or provider HTTP call, and no duplicate return POST is permitted.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

import json

from tests.test_core_0982_loan_return_reconciliation import _library, _loan, Session


PRIVATE = "PRIVATE_RETURN_TOKEN_2026"


def test_inventory_exception_class_name_is_not_persisted_or_replayed(tmp_path):
    class HostileInventoryFailure(RuntimeError):
        def __str__(self):
            raise AssertionError("Do not stringify private inventory failure")
    HostileInventoryFailure.__name__ = PRIVATE
    session = Session([])
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    svc = _library(tmp_path, session=session)
    loan = _loan()
    try:
        other = _loan(loan_id="loan-failed-inventory")
        def failed_inventory(_account):
            raise HostileInventoryFailure(PRIVATE)
        session.loans = failed_inventory
        for selected in (loan, other):
            result = svc.return_digital(selected)
            assert result == {
                "status": "blocked", "code": "loan_return_removed", "retry": "none",
            }
            assert PRIVATE not in json.dumps(result)
        assert session.posts == [] and session.checks == []
        from cifereca.core.loan_return_guard import return_key
        key = return_key("paris", "paris-a", "linked-b", other.id)
        assert svc.storage.loan_return_state(key) is None
    finally:
        svc.close()


def test_dilicom_incomplete_inventory_blocks_confirmation_without_coercing_objects(
    tmp_path,
):
    class HostileUpstream:
        def __str__(self):
            raise AssertionError("No provider-controlled string coercion")
    session = Session([])
    svc = _library(tmp_path, session=session)
    try:
        session.last_loan_errors = [
            {"upstream": HostileUpstream()},
            {"upstream": "DILICOM"},
        ]
        result = svc.return_digital(_loan())
        assert result["status"] == "blocked"
        assert result["code"] == "loan_return_removed"
        assert session.posts == [] and session.checks == []
    finally:
        svc.close()
