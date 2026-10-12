"""P0 #984: Adobe loan return status must preserve exact Paris borrower provenance."""
from __future__ import annotations

from dataclasses import replace

from cifereca.models import Account
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _acquisition(service, loan, *, acquisition_id="acq-exact",
                 profile="bob-profile", item=None, provider_loan_id=None):
    return service.storage.acquisition_put({
        "acquisition_id": acquisition_id,
        "provider_id": loan.source_provider,
        "credential_id": loan.source_credential,
        "account_id": loan.source_account_id,
        "identity_id": "alice",  # loan ledger provenance is the Paris connection
        "item_id": item or loan.record_id,
        "status": "complete",
        "strategy": "loan_auto",
        "executor": "kobo-adept",
        "metadata": {
            "provider_loan_id": loan.id if provider_loan_id is None else provider_loan_id,
            "adept_binding": {
                "adept_profile_id": profile,
                "adept_profile_name": "bob-cli",
                "adept_loan_id": "adobe-loan",
                "loan_state": "active",
                "token_present": True,
                "return_available": True,
            },
        },
    })


def test_linked_borrower_without_adept_grant_has_no_return_action(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _acquisition(service, loan)
        service._adept_return_capability = lambda: True
        result = service.adept_return_status_for_loan(loan)
        assert result == {
            "status": "adept_borrower_binding_required", "return_available": False
        }
    finally:
        service.close()


def test_linked_borrower_uses_confirmed_adobe_profile_not_ui_identity(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        acq = _acquisition(service, loan)
        service._adept_return_capability = lambda: True
        service.current_identity = service.identity_by_id("alice")
        alice_ui = service.adept_return_status_for_loan(loan)
        service.current_identity = service.identity_by_id("bob")
        bob_ui = service.adept_return_status_for_loan(loan)
        assert alice_ui == bob_ui
        assert alice_ui["acquisition_id"] == acq
        assert alice_ui["status"] == "ready"
        assert alice_ui["return_available"] is True
    finally:
        service.close()


def test_wrong_origin_adept_profile_is_not_returnable(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        _acquisition(service, loan, profile="alice-profile")
        service._adept_return_capability = lambda: True
        result = service.adept_return_status_for_loan(loan)
        assert result["status"] == "adept_return_profile_conflict"
        assert result["return_available"] is False
    finally:
        service.close()


def test_binding_revocation_or_profile_rotation_disables_return(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        _acquisition(service, loan)
        service.storage.adept_profile_binding_set("identity", "bob", "other-profile")
        result = service.adept_return_status_for_loan(loan)
        assert result["status"] == "adept_borrower_profile_changed"
        assert result["return_available"] is False
        service.storage.paris_borrower_adept_binding_delete(
            credential_id="alice-paris", account_id="linked-123",
        )
        assert service.adept_return_status_for_loan(loan)["status"] == (
            "adept_borrower_binding_required"
        )
    finally:
        service.close()


def test_unknown_connection_does_not_fallback_to_selected_identity(tmp_path, monkeypatch):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.current_identity = service.identity_by_id("alice")
        monkeypatch.setattr(
            service.storage, "identity_for_connection", lambda *_: None,
        )
        result = service.adept_return_status_for_loan(loan)
        assert result == {
            "status": "adept_identity_unproven", "return_available": False
        }
    finally:
        service.close()


def test_duplicate_complete_acquisitions_cannot_choose_arbitrary_license(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        _acquisition(service, loan, acquisition_id="first")
        _acquisition(service, loan, acquisition_id="second")
        result = service.adept_return_status_for_loan(loan)
        assert result["status"] == "adept_return_acquisition_ambiguous"
        assert result["return_available"] is False
    finally:
        service.close()


def test_same_loan_seen_by_another_identity_disables_adobe_return(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        _acquisition(service, loan)
        service.storage.entitlement_put({
            "entitlement_id": "bob-visible",
            "provider_id": "paris", "credential_id": "bob-paris",
            "account_id": "linked-123", "identity_id": "bob",
            "item_id": loan.record_id, "loan_id": loan.id,
            "entitlement_type": "loan", "state": "active",
        })
        result = service.adept_return_status_for_loan(loan)
        assert result["status"] == "adept_loan_identity_conflict"
        assert result["return_available"] is False
    finally:
        service.close()


def test_wrong_item_or_loan_id_never_matches_acquisition(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        _acquisition(service, loan, item="another-book")
        _acquisition(service, loan, acquisition_id="wrong-loan", provider_loan_id="other-loan")
        result = service.adept_return_status_for_loan(loan)
        assert result["status"] == "no_acquisition"
        assert result["return_available"] is False
    finally:
        service.close()


def test_primary_account_can_use_its_own_proven_profile(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service._adept_return_capability = lambda: True
        primary = replace(loan, source_account_id="primary")
        _acquisition(service, primary, profile="alice-profile")
        result = service.adept_return_status_for_loan(primary)
        assert result["status"] == "ready"
        assert result["return_available"] is True
    finally:
        service.close()
