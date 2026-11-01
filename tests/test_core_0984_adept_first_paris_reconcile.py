"""P1 #4048 — ADEPT/Paris return retired without loss of historical evidence."""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

from types import SimpleNamespace as NS
from dataclasses import replace
from threading import Event
from concurrent.futures import ThreadPoolExecutor

from cifereca.models import Loan, Account
from cifereca.core.return_adept_orchestrator import adept_return_key
from cifereca.core.loan_return_guard import return_key
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


class ParisSession:
    connected = True
    def __init__(self, loans):
        self.inventory = list(loans)
        self.last_loan_errors = []
        self.posts = 0
        self.accounts_read = []

    def loans(self, account):
        self.accounts_read.append(account.unique_id)
        return list(self.inventory)

    def return_digital(self, loan, account):
        self.posts += 1
        return {"success": True}


def setup(tmp_path, *, paris_active=False):
    service, loan = service_with_linked_borrower(tmp_path)
    service.bind_paris_borrower_adept(
        credential_id="alice-paris", account_id="linked-123",
        borrower_identity_id="bob", confirmed=True,
    )
    paris = ParisSession([loan] if paris_active else [])
    service._sessions["paris::alice-paris"] = paris
    service.kobo_profiles = lambda: [{"profile_id": "bob-profile", "name": "bob-cli"}]
    service._adept_return_capability = lambda: True
    service.storage.acquisition_put({
        "acquisition_id": "adept-01",
        "provider_id": "paris", "credential_id": "alice-paris",
        "account_id": "linked-123", "identity_id": "alice",
        "item_id": loan.record_id, "status": "complete",
        "strategy": "loan_auto", "executor": "kobo-adept",
        "metadata": {
            "provider_loan_id": loan.id,
            "adept_binding": {
                "adept_loan_id": "adobe-loan-01",
                "adept_profile_id": "bob-profile",
                "adept_profile_name": "bob-cli",
                "loan_state": "active", "token_present": True,
                "return_available": True,
            },
        },
    })
    return service, loan, paris


def stub_adept(service, *, already_returned=False, failure=False):
    count = {"mutations": 0, "successful_mutations": 0, "inventory": 0}
    def loans(profile):
        assert profile == "bob-cli"
        count["inventory"] += 1
        returned = already_returned or count["successful_mutations"] > 0
        return {"loans": [{
            "id": "adobe-loan-01", "returned": returned,
            "expired": False, "token_present": not returned,
        }]}
    service._kobo_adapter = lambda: NS(loans=loans)

    def mutate(request):
        count["mutations"] += 1
        assert request.operation == "return"
        assert request.metadata["adept_loan_id"] == "adobe-loan-01"
        assert request.metadata["adept_profile_name"] == "bob-cli"
        if failure:
            raise RuntimeError("offline Adobe")
        count["successful_mutations"] += 1
        return {"ok": True, "metadata": {"runtime_result": {
            "ok": True, "operation": "loan_return",
            "loan_id": "adobe-loan-01", "profile": "bob-cli",
        }}}
    service.execute_drm_mutation = mutate
    return count


DENIED = {"status": "blocked", "code": "loan_return_removed", "retry": "none"}


def test_adept_return_retired_preserves_exact_historical_binding(tmp_path):
    service, loan, paris = setup(tmp_path)
    attest_paris_dilicom_complete(paris, credential_id="alice-paris")
    try:
        calls = stub_adept(service)
        before = service.storage.acquisition_get("adept-01")
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert service.storage.acquisition_get("adept-01") == before
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0 and paris.accounts_read == []
    finally:
        service.close()


def test_active_paris_and_adept_never_fall_through_to_pnb(tmp_path):
    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service)
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert service.return_digital(loan, explicit_retry=True) == DENIED
        assert service.return_digital(loan) == DENIED
        assert calls["mutations"] == 0
        assert paris.posts == 0 and paris.accounts_read == []
    finally:
        service.close()


def test_incomplete_inventory_does_not_restart_a_retired_return(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        paris.last_loan_errors = [{"upstream": "DILICOM", "error": "timeout"}]
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert service.verify_loan_return(loan) == {
            "status": "verification_unavailable",
            "code": "loan_return_removed", "retry": "none",
        }
        attest_paris_dilicom_complete(paris, credential_id="alice-paris")
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert calls["mutations"] == 0
        assert paris.posts == 0 and paris.accounts_read == []
    finally:
        service.close()


def test_legacy_provider_checkpoint_is_never_replayed_as_adobe_return(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        pnb_key = return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_claim(pnb_key, "pnbsent")[0]
        before = service.storage.loan_return_state(pnb_key)
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert service.storage.loan_return_state(pnb_key) == before
        assert calls["mutations"] == 0 and paris.posts == 0
    finally:
        service.close()


def test_old_adobe_mutation_failure_is_not_replayed(tmp_path):
    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service, failure=True)
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert calls["mutations"] == 0 and paris.posts == 0
    finally:
        service.close()


def test_adobe_checkpoint_survives_without_any_automatic_recovery(tmp_path):
    service, loan, paris = setup(tmp_path)
    attest_paris_dilicom_complete(paris, credential_id="alice-paris")
    try:
        calls = stub_adept(service, already_returned=True)
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_claim(key, "interrupted")[0]
        service.storage.loan_return_transition(
            key, "interrupted", status="adobe_return_requested",
            verification="adobe_pending", adobe_verified=False,
        )
        before = service.storage.loan_return_state(key)
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert service.verify_loan_return(loan)["code"] == "loan_return_removed"
        assert service.storage.loan_return_state(key) == before
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0
    finally:
        service.close()


def test_initial_verification_never_mutates_or_creates_checkpoint(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        assert service.verify_loan_return(loan) == {
            "status": "verification_unavailable",
            "code": "loan_return_removed", "retry": "none",
        }
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_state(key) is None
        assert calls["mutations"] == 0 and paris.posts == 0
    finally:
        service.close()


def test_revoked_binding_and_non_adept_origin_do_not_enable_return(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        service.storage.paris_borrower_adept_binding_delete(
            credential_id="alice-paris", account_id="linked-123",
        )
        assert service.return_loan_prefer_adept(loan) == DENIED
        service.storage.acquisition_update_state("adept-01", "attention_required")
        assert service.return_loan_prefer_adept(loan) == DENIED
        assert calls["mutations"] == 0 and paris.posts == 0
    finally:
        service.close()


def test_other_connection_loan_uses_same_historical_lock_name(tmp_path):
    service, loan, _paris = setup(tmp_path, paris_active=True)
    try:
        other = replace(loan, source_credential="other-paris", identity_id="bob")
        assert service._return_operation_lock_file(loan) == service._return_operation_lock_file(other)
        assert "alice-paris" not in service._return_operation_lock_file(loan).name
        assert "shared-loan" not in service._return_operation_lock_file(loan).name
    finally:
        service.close()


def test_other_identity_cannot_use_retired_pnb_route(tmp_path):
    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "paris",
            "credential_id": "bob-paris", "label": "Paris Bob",
        })
        service.accounts.append(Account(
            unique_id="linked-123", is_primary=False,
            provider_id="paris", credential_id="bob-paris", identity_id="bob",
        ))
        service.storage.entitlement_put({
            "entitlement_id": "other-view",
            "provider_id": "paris", "credential_id": "bob-paris",
            "account_id": "linked-123", "identity_id": "bob",
            "item_id": loan.record_id, "loan_id": loan.id,
            "entitlement_type": "loan", "state": "active",
        })
        assert service.return_digital(loan, explicit_retry=True) == DENIED
        assert paris.posts == 0 and paris.accounts_read == []
        assert service.storage.loan_return_state(
            return_key("paris", "alice-paris", "linked-123", loan.id)
        ) is None
    finally:
        service.close()
