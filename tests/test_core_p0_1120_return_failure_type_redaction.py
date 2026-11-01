"""P0 #1120: provider and Adobe return exceptions cannot reveal their class names.

All accounts and return operations are fake; only the real SQLite receipt and
guarded Core orchestration run. No provider HTTP or real Adobe mutation.
"""
from __future__ import annotations

import json
import logging

from cifereca.core.loan_return_guard import return_key
from cifereca.core.return_adept_orchestrator import adept_return_key
from tests.test_core_0982_loan_return_reconciliation import (
    _library, _loan, Session,
)
from tests.test_core_0984_adept_first_paris_reconcile import (
    setup as setup_adept_return, stub_adept,
)


PRIVATE = "PRIVATE_RETURN_ACCOUNT_2026"
SIGNED = "https://private.invalid/LOAN/book.acsm?auth=" + PRIVATE


class HostileProviderFailure(RuntimeError):
    def __str__(self):
        raise AssertionError("Raw provider exception was stringified")

    def __repr__(self):
        raise AssertionError("Raw provider exception was repr'ed")


# The class name itself can originate from a dynamic plugin type.
HostileProviderFailure.__name__ = PRIVATE


def test_pnb_return_with_hostile_provider_failure_is_not_dispatched(tmp_path, caplog):
    session = Session([])
    session.failure = HostileProviderFailure(SIGNED)
    service = _library(tmp_path, session=session)
    loan = _loan()
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = service.return_digital(loan)
        assert result == {
            "status": "blocked", "code": "loan_return_removed", "retry": "none",
        }
        assert service.return_digital(loan) == result
        assert session.posts == [] and session.checks == []
        key = return_key("paris", "paris-a", "linked-b", loan.id)
        assert service.storage.loan_return_state(key) is None
        assert PRIVATE not in json.dumps(result) + caplog.text
        assert "private.invalid" not in json.dumps(result) + caplog.text
    finally:
        service.close()


def test_adobe_return_with_hostile_runtime_failure_never_mutates(tmp_path):
    service, loan, paris = setup_adept_return(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service)
        def fail_mutation(_request):
            calls["mutations"] += 1
            raise HostileProviderFailure(SIGNED)
        service.execute_drm_mutation = fail_mutation
        result = service.return_loan_prefer_adept(loan)
        assert result == {
            "status": "blocked", "code": "loan_return_removed", "retry": "none",
        }
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_state(key) is None
        assert PRIVATE not in json.dumps(result)
        assert service.return_loan_prefer_adept(loan) == result
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0
    finally:
        service.close()
