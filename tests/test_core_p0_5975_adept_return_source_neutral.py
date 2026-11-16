"""#5975: historical ADEPT status is read-only and source-neutral.

Real SQLite exact connection/borrower evidence; no provider GET, Adobe runtime,
rights mutation, or voluntary return action can be triggered.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from tests.historical_adept_seed import seed_historical_adept_binding
from tests.test_core_0984_adept_return_status_provenance import _acquisition
from tests.test_core_p0_5975_poststaging_adept_all_sources import (
    _foreign_linked_borrower,
)
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


@pytest.mark.parametrize("provider", [
    "biblioaccess.la-rochelle", "future-library",
])
def test_nonparis_adept_origin_requires_exact_v16_linked_borrower(
    tmp_path, monkeypatch, provider,
):
    service, paris_loan = service_with_linked_borrower(tmp_path)
    try:
        # Historical v15 Paris evidence with the same credential/account
        # must not authorize a different provider.
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        loan = _foreign_linked_borrower(service, paris_loan, provider=provider)
        acquisition = _acquisition(service, loan)
        before = deepcopy(service.storage.acquisition_get(acquisition))
        forbidden = lambda *_a, **_kw: pytest.fail(
            "read-only historical status cannot mutate any ledger"
        )
        monkeypatch.setattr(service.storage, "acquisition_put", forbidden)
        monkeypatch.setattr(service.storage, "entitlement_put", forbidden)
        no_grant = service.adept_return_status_for_loan(loan)
        assert no_grant == {
            "status": "adept_borrower_binding_required",
            "return_available": False,
        }
        # Revocable v16 proof is the only positive borrower authority.
        coords = dict(
            provider_id=provider, credential_id=loan.source_credential,
            account_id=loan.source_account_id,
        )
        service.storage.adept_borrower_grant_put(
            **coords, connection_identity_id="alice",
            borrower_identity_id="bob", profile_id="bob-profile",
            confirmed=True,
        )
        ready = service.adept_return_status_for_loan(loan)
        assert ready["status"] == "ready"
        assert ready["acquisition_id"] == acquisition
        assert ready["return_available"] is False
        service.storage.adept_borrower_grant_delete(**coords)
        assert service.adept_return_status_for_loan(loan) == no_grant
        assert service.storage.acquisition_get(acquisition) == before
    finally:
        service.close()


@pytest.mark.parametrize("provider", [
    "biblioaccess.la-rochelle", "future-library",
])
def test_nonparis_profile_conflict_rejected_for_unattested_origin(
    tmp_path, provider,
):
    service, paris_loan = service_with_linked_borrower(tmp_path)
    try:
        loan = _foreign_linked_borrower(service, paris_loan, provider=provider)
        coords = dict(
            provider_id=provider, credential_id=loan.source_credential,
            account_id=loan.source_account_id,
        )
        service.storage.adept_borrower_grant_put(
            **coords, connection_identity_id="alice",
            borrower_identity_id="bob", profile_id="bob-profile",
            confirmed=True,
        )
        acquisition = _acquisition(service, loan, profile="alice-profile")
        status = service.adept_return_status_for_loan(loan)
        assert status == {
            "status": "adept_return_profile_conflict",
            "acquisition_id": acquisition, "return_available": False,
        }
    finally:
        service.close()


@pytest.mark.parametrize("provider", [
    "biblioaccess.la-rochelle", "future-library",
])
def test_nonparis_borrower_callback_may_not_retarget_historical_ledger(
    tmp_path, monkeypatch, provider,
):
    service, paris_loan = service_with_linked_borrower(tmp_path)
    try:
        loan = _foreign_linked_borrower(service, paris_loan, provider=provider)
        def mutate(*_a, **_kw):
            loan.record_id = "foreign-edition"
            return "bob", None
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", mutate)
        monkeypatch.setattr(
            service.storage, "acquisitions",
            lambda **_kw: pytest.fail("stale source reached private ledger"),
        )
        monkeypatch.setattr(
            service.storage, "entitlements",
            lambda **_kw: pytest.fail("stale source reached rights"),
        )
        assert service.adept_return_status_for_loan(loan) == {
            "status": "adept_identity_unproven", "return_available": False,
        }
    finally:
        service.close()
