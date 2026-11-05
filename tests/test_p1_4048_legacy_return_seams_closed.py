"""P1 #4048: retired voluntary-return entry points never reach the service.

The deliberate poison objects detect *any* attribute access before rejection,
including reads of provider/loan metadata or acquisition/storage state.
"""
from __future__ import annotations

import pytest

from cifereca.core import provider_loan_return, return_adept_orchestrator
from cifereca.plugin_api import AttentionRequired
from cifereca.services import LibraryService


class Poison:
    def __getattribute__(self, _name):
        raise AssertionError("obsolete return accessed a dependency")

    def __str__(self):
        raise AssertionError("obsolete return formatted an argument")

    def __bool__(self):
        raise AssertionError("obsolete return inspected a caller value")


@pytest.mark.parametrize(("method", "kwargs"), [
    ("return_digital", {}),
    ("return_digital", {"explicit_retry": Poison()}),
    ("_return_digital_guarded", {}),
    ("_return_digital_guarded", {"explicit_retry": Poison()}),
    ("return_loan_prefer_adept", {}),
    ("_return_loan_prefer_adept_guarded", {}),
    ("return_adept_acquisition", {}),
    ("_return_adept_acquisition", {}),
])
def test_legacy_service_return_facades_fail_before_all_access(method, kwargs):
    with pytest.raises(AttentionRequired) as err:
        getattr(LibraryService, method)(Poison(), Poison(), **kwargs)
    assert err.value.code == "loan_return_removed"


def test_direct_provider_return_helper_cannot_bypass_service_guard():
    with pytest.raises(AttentionRequired) as err:
        provider_loan_return.return_digital(
            Poison(), Poison(), explicit_retry=Poison(), _TRACE_LOG=Poison(),
        )
    assert err.value.code == "loan_return_removed"


def test_direct_adept_first_helper_cannot_bypass_service_guard():
    with pytest.raises(AttentionRequired) as err:
        return_adept_orchestrator.return_loan_prefer_adept(
            Poison(), Poison(),
            uuid=Poison(), return_ref=Poison(),
            adept_return_key=Poison(), public_state=Poison(),
            AttentionRequired=AttentionRequired, _TRACE_LOG=Poison(),
        )
    assert err.value.code == "loan_return_removed"


def test_historical_adept_receipt_is_read_only_and_never_replayed(tmp_path):
    from cifereca.core.return_adept_orchestrator import adept_return_key
    from tests.test_core_0984_adept_first_paris_reconcile import setup, stub_adept

    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service)
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.verify_loan_return(loan)["status"] == "not_requested"
        claimed, saved = service.storage.loan_return_claim(key, "synthetic-past")
        assert claimed is True
        assert saved["status"] == "request_sent"
        before = service.storage.loan_return_state(key)
        status = service.verify_loan_return(loan)
        assert status["status"] == "request_sent"
        assert status["scope"] == "adobe_first_paris"
        assert status["retry"] == "none"
        assert service.storage.loan_return_state(key) == before
        assert calls["mutations"] == 0 and paris.posts == 0
    finally:
        service.close()
