"""P1 #4048: retired returns do not even invoke hostile provider errors.

Only synthetic sessions/receipts are used; no network or DRM mutation.
"""
from __future__ import annotations

import logging

import pytest

from cifereca.core.loan_return_guard import return_key
from cifereca.core.return_adept_orchestrator import adept_return_key
from cifereca.plugin_api import AttentionRequired
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


HostileProviderFailure.__name__ = PRIVATE


def test_pnb_return_never_calls_hostile_provider_or_writes_failure(tmp_path, caplog):
    session = Session([])
    session.failure = HostileProviderFailure(SIGNED)
    service = _library(tmp_path, session=session)
    loan = _loan()
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            with pytest.raises(AttentionRequired) as err:
                service.return_digital(loan)
        assert err.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
        key = return_key("paris", "paris-a", "linked-b", loan.id)
        assert service.storage.loan_return_state(key) is None
        assert PRIVATE not in caplog.text and "private.invalid" not in caplog.text
    finally:
        service.close()


def test_adobe_return_never_invokes_hostile_runtime_or_writes_checkpoint(tmp_path):
    service, loan, paris = setup_adept_return(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service)
        def fail_mutation(_request):
            calls["mutations"] += 1
            raise HostileProviderFailure(SIGNED)
        service.execute_drm_mutation = fail_mutation
        with pytest.raises(AttentionRequired) as err:
            service.return_loan_prefer_adept(loan)
        assert err.value.code == "loan_return_removed"
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_state(key) is None
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0
    finally:
        service.close()
