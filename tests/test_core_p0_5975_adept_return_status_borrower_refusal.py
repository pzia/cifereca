"""P0 #5975: historical ADEPT status must not treat falsey refusal as grant.

The removed voluntary return operation is not reactivated. Historical
inventory-attested origin can still be displayed after borrower-grant rotation,
but a non-attested row cannot skip the original borrower refusal.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_core_0984_adept_return_status_provenance import _acquisition
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


class HostileRefusal:
    def __bool__(self):
        raise AssertionError("No truthiness of a borrower refusal")

    def __str__(self):
        raise AssertionError("No public coercion of a private borrower refusal")


@pytest.mark.parametrize(("refusal", "public_code"), [
    ("", "adept_borrower_ambiguous"),
    (False, "adept_borrower_ambiguous"),
    (0, "adept_borrower_ambiguous"),
    ([], "adept_borrower_ambiguous"),
    (HostileRefusal(), "adept_borrower_ambiguous"),
    ("https://private.invalid/borrower?token=never-leak", "adept_borrower_ambiguous"),
    ("adept_borrower_binding_required", "adept_borrower_binding_required"),
])
@pytest.mark.parametrize("has_acquisition", [False, True])
def test_non_none_refusal_always_vetoes_unattested_return_status(
    tmp_path, monkeypatch, refusal, public_code, has_acquisition,
):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        if has_acquisition:
            acq = _acquisition(service, loan)
            before = deepcopy(service.storage.acquisition_get(acq))
        else:
            before = None
        before_rows = deepcopy(service.storage.acquisitions(provider_id="paris"))
        before_rights = deepcopy(service.storage.entitlements(provider_id="paris"))
        monkeypatch.setattr(
            service, "_paris_adept_identity_for_loan",
            lambda *_args, **_kwargs: ("bob", refusal),
        )
        result = service.adept_return_status_for_loan(loan)
        assert result == {"status": public_code, "return_available": False}
        assert service.storage.acquisitions(provider_id="paris") == before_rows
        assert service.storage.entitlements(provider_id="paris") == before_rights
        if before is not None:
            assert service.storage.acquisition_get(acq) == before
    finally:
        service.close()


def test_no_refusal_and_no_acquisition_retains_no_acquisition_status(tmp_path, monkeypatch):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        monkeypatch.setattr(
            service, "_paris_adept_identity_for_loan",
            lambda *_a, **_kw: ("bob", None),
        )
        assert service.adept_return_status_for_loan(loan) == {
            "status": "no_acquisition", "return_available": False,
        }
    finally:
        service.close()


def test_inventory_attested_origin_survives_revoked_or_hostile_grant(
    tmp_path, monkeypatch,
):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        acq = _acquisition(service, loan)
        row = service.storage.acquisition_get(acq)
        metadata = deepcopy(row["metadata"])
        metadata["adept_binding"].update(
            inventory_status="resolved", inventory_checked_at=1234,
            binding_source="public_inventory", expired=False,
            return_verification="active_confirmed",
        )
        service.storage.acquisition_put({**row, "metadata": metadata})
        before = deepcopy(service.storage.acquisition_get(acq))
        monkeypatch.setattr(
            service, "_paris_adept_identity_for_loan",
            lambda *_a, **_kw: ("bob", HostileRefusal()),
        )
        result = service.adept_return_status_for_loan(loan)
        assert result["status"] == "ready"
        assert result["return_available"] is False
        assert result["acquisition_id"] == acq
        assert service.storage.acquisition_get(acq) == before
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-library"])
@pytest.mark.parametrize("evidence", ["none", "unattested", "attested"])
@pytest.mark.parametrize("borrower", [None, "", False, 0, HostileRefusal(), "exception"])
def test_historical_status_requires_positive_borrower_or_attested_origin(
    tmp_path, monkeypatch, provider, evidence, borrower,
):
    from tests.test_core_p0_5975_poststaging_adept_all_sources import (
        _foreign_linked_borrower,
    )

    service, loan = service_with_linked_borrower(tmp_path)
    try:
        if provider != "paris":
            loan = _foreign_linked_borrower(service, loan, provider=provider)
        acquisition = None
        if evidence != "none":
            acquisition = _acquisition(service, loan)
            if evidence == "attested":
                row = service.storage.acquisition_get(acquisition)
                metadata = deepcopy(row["metadata"])
                metadata["adept_binding"].update(
                    inventory_status="resolved", inventory_checked_at=1234,
                    binding_source="public_inventory", expired=False,
                    return_verification="active_confirmed",
                )
                service.storage.acquisition_put({**row, "metadata": metadata})
        before_rows = deepcopy(service.storage.acquisitions(provider_id=provider))
        before_rights = deepcopy(service.storage.entitlements(provider_id=provider))

        def resolve(*_args, **_kwargs):
            if type(borrower) is str and borrower == "exception":
                raise RuntimeError("PRIVATE_PROVIDER_CREDENTIAL")
            return borrower, None

        def forbidden(*_args, **_kwargs):
            pytest.fail("historical status cannot read a live profile or write rights")

        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", resolve)
        monkeypatch.setattr(service.storage, "adept_profile_binding_get", forbidden)
        monkeypatch.setattr(service.storage, "acquisition_put", forbidden)
        monkeypatch.setattr(service.storage, "entitlement_put", forbidden)
        monkeypatch.setattr(service, "kobo_profiles", forbidden)
        result = service.adept_return_status_for_loan(loan)
        if evidence == "attested":
            assert result["status"] == "ready"
            assert result["acquisition_id"] == acquisition
            assert result["return_available"] is False
        else:
            assert result == {
                "status": "adept_borrower_ambiguous", "return_available": False,
            }
        assert service.storage.acquisitions(provider_id=provider) == before_rows
        assert service.storage.entitlements(provider_id=provider) == before_rights
    finally:
        service.close()
