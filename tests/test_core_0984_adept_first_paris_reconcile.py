"""P0 #984 — ADEPT-first Paris return, without duplicate mutation or PNB POST."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.models import Loan
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
    count = {"mutations": 0, "inventory": 0}
    def loans(profile):
        assert profile == "bob-cli"
        count["inventory"] += 1
        returned = already_returned or count["mutations"] > 0
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
        return {"ok": True, "metadata": {"runtime_result": {
            "ok": True, "operation": "loan_return",
            "loan_id": "adobe-loan-01", "profile": "bob-cli",
        }}}
    service.execute_drm_mutation = mutate
    return count


def test_adept_return_then_paris_absence_confirmed_without_pnb_post(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        first = service.return_loan_prefer_adept(loan)
        assert first["status"] == "paris_return_confirmed"
        assert first["adobe_return_confirmed"] is True
        assert first["provider_return_confirmed"] is True
        assert first["paris_verification"] == "provider_absent"
        assert calls["mutations"] == 1
        assert paris.posts == 0
        assert paris.accounts_read == ["linked-123"]
        assert service.return_loan_prefer_adept(loan) == first
        assert calls["mutations"] == 1
        assert paris.posts == 0
        binding = service.storage.acquisition_get("adept-01")["metadata"]["adept_binding"]
        assert binding["return_verification"] == "confirmed"
        assert binding["token_present"] is False
    finally:
        service.close()


def test_adept_return_paris_still_active_requires_separate_explicit_pnb(tmp_path):
    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service)
        first = service.return_loan_prefer_adept(loan)
        assert first["status"] == "partial"
        assert first["adobe_return_confirmed"] is True
        assert first["paris_verification"] == "still_present"
        assert first["retry"] == "provider_action_explicit"
        assert paris.posts == 0
        assert service.return_digital(loan)["reason"] == "adobe_return_takes_precedence"
        assert paris.posts == 0
        assert service.return_loan_prefer_adept(loan)["status"] == "partial"
        assert calls["mutations"] == 1
        assert paris.posts == 0
        provider = service.return_digital(loan, explicit_retry=True)
        assert provider["status"] == "still_active"
        assert paris.posts == 1
        assert calls["mutations"] == 1
    finally:
        service.close()


def test_adept_return_verification_unavailable_never_claims_paris_absence(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        stub_adept(service)
        paris.last_loan_errors = [{"upstream": "DILICOM", "error": "timeout"}]
        result = service.return_loan_prefer_adept(loan)
        assert result["status"] == "partial"
        assert result["paris_verification"] == "unavailable"
        assert result["adobe_return_confirmed"] is True
        assert paris.posts == 0
        paris.last_loan_errors = []
        assert service.verify_loan_return(loan)["status"] == "paris_return_confirmed"
        assert paris.posts == 0
    finally:
        service.close()


def test_provider_return_requested_first_blocks_any_adobe_mutation(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        pnb_key = return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_claim(pnb_key, "pnbsent")[0]
        result = service.return_loan_prefer_adept(loan)
        assert result["status"] == "partial"
        assert result["reason"] == "provider_return_already_requested"
        assert calls["mutations"] == 0
        assert paris.posts == 0
    finally:
        service.close()


def test_adobe_mutation_failure_persists_and_cannot_be_replayed(tmp_path):
    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service, failure=True)
        result = service.return_loan_prefer_adept(loan)
        assert result["status"] == "failed"
        assert result["adobe_return_confirmed"] is False
        assert service.return_loan_prefer_adept(loan)["status"] == "failed"
        assert calls["mutations"] == 1
        assert paris.posts == 0
    finally:
        service.close()


def test_crash_after_adobe_call_recovers_from_inventory_only(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service, already_returned=True)
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_claim(key, "interrupted")[0]
        service.storage.loan_return_transition(
            key, "interrupted", status="adobe_return_requested",
            verification="adobe_pending", adobe_verified=False,
        )
        outcome = service.return_loan_prefer_adept(loan)
        assert outcome["status"] == "paris_return_confirmed"
        assert outcome["adobe_return_confirmed"] is True
        assert calls["mutations"] == 0
        assert calls["inventory"] == 1
        assert paris.posts == 0
    finally:
        service.close()


def test_verify_loan_return_initially_never_mutates_or_creates_checkpoint(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        assert service.verify_loan_return(loan) == {
            "status": "not_requested", "scope": "adobe_first_paris",
        }
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_state(key) is None
        assert calls["mutations"] == 0
        assert paris.posts == 0
    finally:
        service.close()


def test_loan_with_revoked_borrower_binding_cannot_return(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        service.storage.paris_borrower_adept_binding_delete(
            credential_id="alice-paris", account_id="linked-123",
        )
        result = service.return_loan_prefer_adept(loan)
        assert result["status"] == "adept_borrower_binding_required"
        assert calls["mutations"] == 0
        assert paris.posts == 0
    finally:
        service.close()


def test_non_adept_loan_only_offers_distinct_provider_action(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        service.storage.acquisition_update_state("adept-01", "attention_required")
        result = service.return_loan_prefer_adept(loan)
        assert result["status"] == "provider_action_available"
        assert result["retry"] == "provider_action_explicit"
        assert calls["mutations"] == 0
        assert paris.posts == 0
    finally:
        service.close()
