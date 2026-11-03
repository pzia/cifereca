"""P1 #4048 — historical Paris/ADEPT return fixtures and retired-entry guards."""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

from types import SimpleNamespace as NS
from dataclasses import replace

import pytest
from cifereca.plugin_api import AttentionRequired

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



@pytest.mark.parametrize("paris_active", [False, True])
def test_retired_adept_first_is_blocked_before_own_claim_or_provider(tmp_path, paris_active):
    service, loan, paris = setup(tmp_path, paris_active=paris_active)
    try:
        calls = stub_adept(service)
        pnb_key = return_key("paris", "alice-paris", "linked-123", loan.id)
        adept_key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        before = service.storage.acquisition_get("adept-01")
        for _ in range(2):
            with pytest.raises(AttentionRequired) as result:
                service.return_loan_prefer_adept(loan)
            assert result.value.code == "loan_return_removed"
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0 and paris.accounts_read == []
        assert service.storage.loan_return_state(adept_key) is None
        assert service.storage.loan_return_state(pnb_key) is None
        assert service.storage.acquisition_get("adept-01") == before
    finally:
        service.close()


@pytest.mark.parametrize("explicit_retry", [False, True, "true", None])
def test_retired_pnb_return_cannot_replay_adobe_or_mutate_pnb(tmp_path, explicit_retry):
    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service)
        for _ in range(2):
            with pytest.raises(AttentionRequired) as result:
                service.return_digital(loan, explicit_retry=explicit_retry)
            assert result.value.code == "loan_return_removed"
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0 and paris.accounts_read == []
        assert service.storage.loan_return_state(
            return_key("paris", "alice-paris", "linked-123", loan.id),
        ) is None
    finally:
        service.close()


def test_legacy_return_claim_is_preserved_not_advanced_by_retired_apis(tmp_path):
    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service, failure=True)
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_claim(key, "before-upgrade")[0] is True
        before = service.storage.loan_return_state(key)
        assert before["status"] == "request_sent"
        assert service.verify_loan_return(loan)["status"] == "request_sent"
        assert service.verify_loan_return(loan)["retry"] == "none"
        for operation in (service.return_loan_prefer_adept, service.return_digital):
            with pytest.raises(AttentionRequired) as result:
                operation(loan)
            assert result.value.code == "loan_return_removed"
        assert service.storage.loan_return_state(key) == before
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0 and paris.accounts_read == []
    finally:
        service.close()


def test_legacy_verified_adobe_receipt_remains_readable_without_ever_replaying(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service, already_returned=True)
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_claim(key, "before-upgrade")[0] is True
        stored = service.storage.loan_return_transition(
            key, "before-upgrade", status="adobe_return_confirmed",
            verification="adobe_confirmed", adobe_verified=True,
        )
        assert stored is not None
        before = service.storage.loan_return_state(key)
        result = service.verify_loan_return(loan)
        assert result["status"] == "adobe_return_confirmed"
        assert result["adobe_return_confirmed"] is True
        assert result["retry"] == "none"
        assert service.storage.loan_return_state(key) == before
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0 and paris.accounts_read == []
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
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0
    finally:
        service.close()


def test_retired_return_cannot_bypass_revoked_borrower_binding(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        service.storage.paris_borrower_adept_binding_delete(
            credential_id="alice-paris", account_id="linked-123",
        )
        with pytest.raises(AttentionRequired) as result:
            service.return_loan_prefer_adept(loan)
        assert result.value.code == "loan_return_removed"
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0
    finally:
        service.close()


def test_retired_return_cannot_advertise_provider_fallback(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service)
        service.storage.acquisition_update_state("adept-01", "attention_required")
        with pytest.raises(AttentionRequired) as result:
            service.return_loan_prefer_adept(loan)
        assert result.value.code == "loan_return_removed"
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0
    finally:
        service.close()


def test_concurrent_retired_adobe_pnb_commands_fail_before_any_mutation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        calls = stub_adept(service)
        with ThreadPoolExecutor(max_workers=2) as pool:
            adobe = pool.submit(service.return_loan_prefer_adept, loan)
            pnb = pool.submit(service.return_digital, loan, explicit_retry=True)
            for future in (adobe, pnb):
                with pytest.raises(AttentionRequired) as result:
                    future.result(timeout=10)
                assert result.value.code == "loan_return_removed"
        assert calls["mutations"] == calls["inventory"] == 0
        assert paris.posts == 0
        assert service.storage.loan_return_state(
            adept_return_key("paris", "alice-paris", "linked-123", loan.id),
        ) is None
        assert service.storage.loan_return_state(
            return_key("paris", "alice-paris", "linked-123", loan.id),
        ) is None
    finally:
        service.close()


def test_other_connection_same_upstream_loan_keeps_historical_lock_scope(tmp_path):
    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        other = replace(loan, source_credential="other-paris", identity_id="bob")
        assert service._return_operation_lock_file(loan) == service._return_operation_lock_file(other)
        assert "alice-paris" not in service._return_operation_lock_file(loan).name
        assert "shared-loan" not in service._return_operation_lock_file(loan).name
    finally:
        service.close()


def test_secondary_connection_and_foreign_loan_can_never_initiate_pnb_return(tmp_path):
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
        before = service.storage.acquisitions(provider_id="paris")
        with pytest.raises(AttentionRequired) as result:
            service.return_digital(loan, explicit_retry=True)
        assert result.value.code == "loan_return_removed"
        assert paris.posts == 0
        assert service.storage.acquisitions(provider_id="paris") == before
        assert service.storage.loan_return_state(
            return_key("paris", "alice-paris", "linked-123", loan.id),
        ) is None
    finally:
        service.close()
