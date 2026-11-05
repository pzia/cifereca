"""P1 #4048: retired provider return never exposes private inventory failures.

Old inventory read / retry behavior is no longer an executable return path.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import AttentionRequired
from tests.test_core_0982_loan_return_reconciliation import _library, _loan, Session

PRIVATE = "PRIVATE_RETURN_TOKEN_2026"


def test_inventory_exception_class_name_never_reaches_retired_return(tmp_path, caplog):
    class HostileInventoryFailure(RuntimeError):
        def __str__(self):
            raise AssertionError("Do not stringify private inventory failure")
    HostileInventoryFailure.__name__ = PRIVATE

    session = Session([])
    def failed_inventory(_account):
        raise HostileInventoryFailure(PRIVATE)
    session.loans = failed_inventory
    service = _library(tmp_path, session=session)
    loan = _loan()
    try:
        with pytest.raises(AttentionRequired) as result:
            service.return_digital(loan)
        assert result.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
        assert service.loan_return_state(loan) is None
        assert PRIVATE not in caplog.text
    finally:
        service.close()


def test_malformed_inventory_objects_are_not_coerced_or_queried(tmp_path):
    class HostileUpstream:
        def __str__(self):
            raise AssertionError("No provider-controlled string coercion")
    session = Session([])
    session.last_loan_errors = [
        {"upstream": HostileUpstream()}, {"upstream": "DILICOM"},
    ]
    service = _library(tmp_path, session=session)
    loan = _loan()
    try:
        with pytest.raises(AttentionRequired) as result:
            service.return_digital(loan)
        assert result.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
        assert service.loan_return_state(loan) is None
    finally:
        service.close()
