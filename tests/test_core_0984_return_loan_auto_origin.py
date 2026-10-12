"""P0 #984 / historical draft #989: return only an exact loan_auto acquisition.

Manual/imported ADEPT artifacts must not authorize the Paris/Adobe loan-return
button, even with coinciding item/title/account and plausible binding metadata.
These checks never call Adobe or Paris network services.
"""
from __future__ import annotations

import pytest

from tests.test_core_0984_adept_return_status_provenance import _acquisition
from tests.test_core_0984_adept_first_paris_reconcile import setup
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _confirmed_service(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    service.bind_paris_borrower_adept(
        credential_id="alice-paris", account_id="linked-123",
        borrower_identity_id="bob", confirmed=True,
    )
    service._adept_return_capability = lambda: True
    return service, loan


@pytest.mark.parametrize("strategy", [None, "manual", "import", "adept_manual"])
def test_unrelated_completed_strategy_cannot_authorize_adobe_return(tmp_path, strategy):
    service, loan = _confirmed_service(tmp_path)
    try:
        acquisition_id = _acquisition(service, loan, acquisition_id="unrelated")
        row = service.storage.acquisition_get(acquisition_id)
        service.storage.acquisition_put({**row, "strategy": strategy})
        before = service.storage.acquisition_get(acquisition_id)
        state = service.adept_return_status_for_loan(loan)
        assert state == {"status": "no_acquisition", "return_available": False}
        assert service.storage.acquisition_get(acquisition_id) == before
    finally:
        service.close()


def test_other_strategy_does_not_make_exact_loan_auto_ambiguous(tmp_path):
    service, loan = _confirmed_service(tmp_path)
    try:
        manual_id = _acquisition(service, loan, acquisition_id="unrelated-manual")
        manual_row = service.storage.acquisition_get(manual_id)
        service.storage.acquisition_put({**manual_row, "strategy": "manual"})
        canonical_id = _acquisition(service, loan, acquisition_id="loan-auto")
        state = service.adept_return_status_for_loan(loan)
        assert state["status"] == "ready"
        assert state["acquisition_id"] == canonical_id
        assert state["return_available"] is True
    finally:
        service.close()


def test_return_action_with_manual_only_never_claims_or_mutates(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        row = service.storage.acquisition_get("adept-01")
        service.storage.acquisition_put({**row, "strategy": "manual"})
        service.execute_drm_mutation = lambda _req: pytest.fail("Adobe mutation forbidden")
        result = service.return_loan_prefer_adept(loan)
        assert result["status"] == "provider_action_available"
        assert result["retry"] == "provider_action_explicit"
        assert paris.posts == 0
        assert service.verify_loan_return(loan)["status"] == "not_requested"
    finally:
        service.close()
