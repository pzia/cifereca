"""P0 #984: Adobe-first loan return orchestration, no network or secret leaks."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, Loan
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.core.return_adept_orchestrator import (
    acquisition_for_loan, adept_return_key,
)


class Paris:
    provider_id = "paris"
    connected = True
    capabilities = NS(loans=True)
    last_loan_errors = []
    def __init__(self, loans):
        self.inventory = list(loans)
        self.posts = 0
        self.read_accounts = []
        self.last_loan_errors = []

    def loans(self, account):
        self.read_accounts.append(account.unique_id)
        return list(self.inventory)

    def return_digital(self, loan, account):
        self.posts += 1
        return {"success": True}


def _loan(credential="paris-a", borrower="linked-b", loan_id="loan-pnb-1"):
    return Loan(
        id=loan_id, provider="DILICOM", medium="digital",
        record_id="book-a", title="Livre", source_provider="paris",
        source_credential=credential, source_account_id=borrower,
        identity_id="default", can_return=True,
    )


def _service(tmp_path, paris, *, multi=False):
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "library"))
    cfg.set("providers.accounts", [])
    if multi:
        cfg.set("identities", [
            {"id": "default", "label": "Login A", "directory_name": "login-a",
             "preferences": {"reading_languages": ["fr"]}},
            {"id": "alex", "label": "Borrower B", "directory_name": "borrower-b",
             "preferences": {"reading_languages": ["fr"]}},
        ])
    svc = LibraryService(cfg, Storage(tmp_path / "cifereca.sqlite3"), Diagnostics())
    svc.storage.provider_connection_put({
        "identity_id": "default", "provider_id": "paris",
        "credential_id": "paris-a", "label": "Paris A",
    })
    svc.accounts = [
        Account(display_name="Paris A", provider_id="paris",
                credential_id="paris-a", unique_id="", is_primary=True,
                identity_id="default"),
        Account(display_name="Paris linked", provider_id="paris",
                credential_id="paris-a", unique_id="linked-b", is_primary=False,
                identity_id="default"),
    ]
    svc._sessions["paris::paris-a"] = paris
    svc.kobo_profiles = lambda: [
        {"profile_id": "adept-stable", "name": "alex-adept"},
        {"profile_id": "adept-alex", "name": "alex-adept"},
    ]
    svc._adept_return_capability = lambda: True
    svc.storage.adept_profile_binding_set("identity", "default", "adept-stable")
    if multi:
        svc.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "paris-b", "label": "Paris B",
        })
        svc.storage.adept_profile_binding_set("identity", "alex", "adept-alex")
        svc.accounts.append(
            Account(display_name="Paris borrower B", provider_id="paris",
                    credential_id="paris-b", unique_id="", is_primary=True,
                    identity_id="alex"),
        )
        svc._sessions["paris::paris-b"] = Paris([])
    else:
        svc.confirm_adept_borrower(
            provider_id="paris", credential_id="paris-a",
            account_id="linked-b", identity_id="default",
            profile_id="adept-stable", confirmed=True,
        )
    return svc


def _acquisition(svc, tmp_path, *, loan=None, source_account_id="linked-b",
                 profile_id="adept-stable", adobe_loan_id="adept-loan-1",
                 identity_id="default"):
    loan = loan or _loan()
    artifact = tmp_path / "unmodified.epub"
    artifact.write_bytes(b"pre-existing")
    svc.storage.acquisition_put({
        "acquisition_id": "acq-adept-1", "identity_id": identity_id,
        "provider_id": "paris", "credential_id": loan.source_credential,
        "account_id": source_account_id, "item_id": str(loan.record_id),
        "title": "Livre", "status": "complete", "strategy": "loan_auto",
        "executor": "kobo-adept",
        "metadata": {
            "provider_loan_id": loan.id, "path": str(artifact),
            "adept_binding": {
                "adept_loan_id": adobe_loan_id,
                "adept_profile_id": profile_id,
                "adept_profile_name": "alex-adept",
                "loan_state": "active", "token_present": True,
                "return_available": True,
            },
        },
    })
    return artifact


def _adept_mock(svc, *, returned_after=True, failure=False):
    events = []
    def loan_inventory(profile):
        events.append(("adept_inventory", profile))
        returned = sum(k == "adept_inventory" for k, _ in events) > 1
        if returned_after is False:
            returned = False
        return {"loans": [{
            "id": "adept-loan-1", "returned": returned,
            "expired": False, "token_present": not returned,
        }]}
    svc._kobo_adapter = lambda: NS(loans=loan_inventory)
    def mutation(request):
        events.append(("adept_mutation", request.operation))
        if failure:
            raise RuntimeError("Adobe unavailable")
        assert request.operation == "return"
        assert request.metadata["adept_loan_id"] == "adept-loan-1"
        assert request.metadata["adept_profile_name"] == "alex-adept"
        return {"ok": True, "metadata": {"runtime_result": {
            "ok": True, "operation": "loan_return",
            "loan_id": "adept-loan-1", "profile": "alex-adept",
        }}}
    svc.execute_drm_mutation = mutation
    return events


def test_adept_confirmed_paris_absent_only_one_mutation(tmp_path):
    loan = _loan()
    paris = Paris([])
    svc = _service(tmp_path, paris)
    artifact = _acquisition(svc, tmp_path)
    try:
        events = _adept_mock(svc)
        first = svc.return_loan_prefer_adept(loan)
        assert first["status"] == "paris_return_confirmed"
        assert first["adobe_return_confirmed"] is True
        assert first["provider_return_confirmed"] is True
        assert first["paris_verification"] == "provider_absent"
        assert sum(name == "adept_mutation" for name, _ in events) == 1
        assert paris.read_accounts == ["linked-b"]
        assert paris.posts == 0
        assert artifact.read_bytes() == b"pre-existing"
        assert svc.return_loan_prefer_adept(loan) == first
        assert sum(name == "adept_mutation" for name, _ in events) == 1
        assert paris.posts == 0
    finally:
        svc.close()


def test_adept_confirmed_paris_still_active_requires_explicit_provider_action(tmp_path):
    loan = _loan()
    paris = Paris([loan])
    svc = _service(tmp_path, paris)
    _acquisition(svc, tmp_path)
    try:
        events = _adept_mock(svc)
        first = svc.return_loan_prefer_adept(loan)
        assert first["status"] == "partial"
        assert first["adobe_return_confirmed"] is True
        assert first["provider_return_confirmed"] is False
        assert first["paris_verification"] == "still_present"
        assert first["retry"] == "provider_action_explicit"
        assert paris.posts == 0
        assert svc.return_digital(loan)["reason"] == "adobe_return_takes_precedence"
        assert paris.posts == 0
        again = svc.return_loan_prefer_adept(loan)
        assert again["status"] == "partial"
        assert paris.posts == 0
        assert sum(name == "adept_mutation" for name, _ in events) == 1
        # A separate explicit provider action is the only path to PNB POST.
        result = svc.return_digital(loan, explicit_retry=True)
        assert result["status"] == "still_active"
        assert paris.posts == 1
        assert paris.read_accounts == ["linked-b", "linked-b", "linked-b", "linked-b"]
    finally:
        svc.close()


def test_adept_return_failure_is_persisted_and_not_replayed(tmp_path):
    loan = _loan()
    paris = Paris([loan])
    svc = _service(tmp_path, paris)
    _acquisition(svc, tmp_path)
    try:
        events = _adept_mock(svc, failure=True, returned_after=False)
        first = svc.return_loan_prefer_adept(loan)
        assert first["status"] == "failed"
        assert first["adobe_return_confirmed"] is False
        assert paris.posts == 0
        retry = svc.return_loan_prefer_adept(loan)
        assert retry["status"] == "failed"
        assert sum(name == "adept_mutation" for name, _ in events) == 1
        assert paris.posts == 0
    finally:
        svc.close()


def test_missing_binding_or_unknown_exact_loan_never_attempts_adept_or_paris(tmp_path):
    loan = _loan()
    paris = Paris([loan])
    svc = _service(tmp_path, paris)
    try:
        svc.execute_drm_mutation = lambda x: pytest.fail("unproven ADEPT mutation")
        assert svc.verify_loan_return(loan)["status"] == "not_requested"
        assert svc.return_loan_prefer_adept(loan)["status"] == "provider_action_available"
        artifact = _acquisition(svc, tmp_path)
        assert svc.return_loan_prefer_adept(replace(loan, id="other-loan"))["status"] == "provider_action_available"
        svc.storage.acquisition_update_state(
            "acq-adept-1", "complete", metadata={
                "provider_loan_id": loan.id,
                "adept_binding": {"adept_profile_id": "adept-stable"},
            },
        )
        result = svc.return_loan_prefer_adept(loan)
        assert result["status"] == "adept_return_binding_required"
        assert paris.posts == 0
        assert artifact.exists()
    finally:
        svc.close()


def test_ambiguous_identity_or_subaccount_never_selects_runtime(tmp_path):
    loan = _loan()
    paris = Paris([loan])
    svc = _service(tmp_path, paris)
    _acquisition(svc, tmp_path)
    try:
        svc.execute_drm_mutation = lambda x: pytest.fail("ADEPT should not run")
        assert svc.return_loan_prefer_adept(replace(loan, identity_id="different"))["status"] == "failed"
        assert svc.return_loan_prefer_adept(replace(loan, source_account_id="primary"))["status"] == "failed"
        assert svc.return_loan_prefer_adept(replace(loan, source_account_id=None))["status"] == "failed"
        assert paris.posts == 0
    finally:
        svc.close()


def test_adept_confirmed_but_paris_verification_unavailable(tmp_path):
    loan = _loan()
    paris = Paris([])
    paris.last_loan_errors = [{"upstream": "DILICOM", "type": "TimeoutError"}]
    svc = _service(tmp_path, paris)
    _acquisition(svc, tmp_path)
    try:
        _adept_mock(svc)
        result = svc.return_loan_prefer_adept(loan)
        assert result["status"] == "partial"
        assert result["paris_verification"] == "unavailable"
        assert result["adobe_return_confirmed"] is True
        assert paris.posts == 0
        paris.last_loan_errors = []
        refreshed = svc.verify_loan_return(loan)
        assert refreshed["status"] == "paris_return_confirmed"
        assert paris.posts == 0
    finally:
        svc.close()


def test_crash_between_adobe_mutation_and_sqlite_confirmation_resumes_read_only(tmp_path):
    loan = _loan()
    paris = Paris([])
    svc = _service(tmp_path, paris)
    _acquisition(svc, tmp_path)
    key = adept_return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        assert svc.storage.loan_return_claim(key, "crashed")[0]
        svc.storage.loan_return_transition(
            key, "crashed", status="adobe_return_requested",
            verification="adobe_pending", adobe_verified=False,
        )
    finally:
        svc.close()
    restarted = _service(tmp_path, paris)
    try:
        calls = []
        restarted._kobo_adapter = lambda: NS(loans=lambda profile: (
            calls.append(profile) or {"loans": [{
                "id": "adept-loan-1", "returned": True, "token_present": False,
            }]}
        ))
        restarted.execute_drm_mutation = lambda _: pytest.fail("cannot replay Adobe")
        outcome = restarted.return_loan_prefer_adept(loan)
        assert outcome["status"] == "paris_return_confirmed"
        assert calls == ["alex-adept"]
        assert paris.posts == 0
    finally:
        restarted.close()


def test_provider_mutation_preexisting_prevents_adobe_mutation(tmp_path):
    loan = _loan()
    paris = Paris([])
    svc = _service(tmp_path, paris)
    _acquisition(svc, tmp_path)
    try:
        from cifereca.core.loan_return_guard import return_key
        key = return_key("paris", "paris-a", "linked-b", loan.id)
        assert svc.storage.loan_return_claim(key, "pnbsent")[0]
        svc.execute_drm_mutation = lambda _: pytest.fail("must reconcile PNB first")
        result = svc.return_loan_prefer_adept(loan)
        assert result["status"] == "partial"
        assert result["reason"] == "provider_return_already_requested"
        assert paris.posts == 0
    finally:
        svc.close()


def test_source_matching_never_uses_title_or_unscoped_acquisition():
    row = {
        "identity_id": "default", "provider_id": "paris", "credential_id": "paris-a",
        "account_id": "linked-b", "item_id": "book-a", "strategy": "loan_auto",
        "executor": "kobo-adept", "status": "complete",
        "metadata": {
            "provider_loan_id": "loan-pnb-1",
            "adept_binding": {"adept_loan_id": "adept-loan-1",
                              "adept_profile_id": "adept-stable"},
        },
    }
    kwargs = dict(
        provider_id="paris", credential_id="paris-a", borrower_id="linked-b",
        loan_id="loan-pnb-1", item_id="book-a", identity_id="default",
    )
    assert acquisition_for_loan([row], **kwargs)[0] == "adobe_bound"
    assert acquisition_for_loan([{**row, "credential_id": "paris-b"}], **kwargs)[0] == "provider_action_available"
    assert acquisition_for_loan([{**row, "item_id": "other-book"}], **kwargs)[0] == "provider_action_available"
    assert acquisition_for_loan([row, row], **kwargs)[0] == "adept_return_ambiguous"

def test_profile_rotation_blocks_adobe_before_mutation(tmp_path):
    loan = _loan()
    svc = _service(tmp_path, Paris([]))
    _acquisition(svc, tmp_path)
    try:
        svc.storage.adept_profile_binding_set("identity", "default", "another-profile")
        svc.execute_drm_mutation = lambda *_: pytest.fail("different Adobe profile")
        state = svc.return_loan_prefer_adept(loan)
        assert state["status"] == "failed"
        assert state["code"] == "adept_borrower_profile_unproven"
    finally:
        svc.close()

def test_linked_borrower_other_identity_returns_with_origin_profile_only(tmp_path):
    """The Paris login identity is NOT the ADEPT borrower identity (#976/#984)."""
    source_loan = _loan()  # Login Paris A displays a linked-b borrower.
    paris = Paris([])
    svc = _service(tmp_path, paris, multi=True)
    artifact = _acquisition(
        svc, tmp_path, loan=source_loan, profile_id="adept-alex",
        identity_id="alex",
    )
    try:
        svc.confirm_adept_borrower(
            provider_id="paris", credential_id="paris-a", account_id="linked-b",
            identity_id="alex", profile_id="adept-alex", confirmed=True,
        )
        events = _adept_mock(svc)
        # Multiple Paris identity credentials: even an explicitly linked
        # borrower may NOT fulfill/return a shared loan before owner choice.
        initial = svc.return_loan_prefer_adept(source_loan)
        assert initial["status"] == "failed"
        assert initial["code"] == "adept_loan_owner_ambiguous"
        assert events == []
        assert paris.posts == 0

        svc.confirm_adept_loan_owner(source_loan, identity_id="alex", confirmed=True)
        svc.select_identity("default")
        done = svc.return_loan_prefer_adept(source_loan)
        assert done["status"] == "paris_return_confirmed"
        assert done["adobe_return_confirmed"] is True
        assert paris.read_accounts == ["linked-b"]
        assert paris.posts == 0
        assert artifact.read_bytes() == b"pre-existing"
        assert sum(name == "adept_mutation" for name, _ in events) == 1

        # B can see the same upstream loan, but never steals A's owner proof.
        svc.confirm_adept_borrower(
            provider_id="paris", credential_id="paris-b", account_id="primary",
            identity_id="alex", profile_id="adept-alex", confirmed=True,
        )
        other = replace(
            source_loan, source_credential="paris-b",
            source_account_id="primary", identity_id="alex",
        )
        second = svc.return_loan_prefer_adept(other)
        assert second["status"] == "failed"
        assert second["code"] == "adept_loan_owner_ambiguous"
        assert svc._sessions["paris::paris-b"].posts == 0
        assert sum(name == "adept_mutation" for name, _ in events) == 1
    finally:
        svc.close()


def test_cross_identity_rejection_on_changed_stable_profile(tmp_path):
    loan = _loan()
    svc = _service(tmp_path, Paris([loan]), multi=True)
    _acquisition(svc, tmp_path, loan=loan, profile_id="adept-alex",
                 identity_id="alex")
    try:
        svc.confirm_adept_borrower(
            provider_id="paris", credential_id="paris-a", account_id="linked-b",
            identity_id="alex", profile_id="adept-alex", confirmed=True,
        )
        svc.confirm_adept_loan_owner(loan, identity_id="alex", confirmed=True)
        svc.storage.adept_profile_binding_set("identity", "alex", "rotated-adept-profile")
        svc.execute_drm_mutation = lambda _: pytest.fail("no mutation with stale binding")
        result = svc.return_loan_prefer_adept(loan)
        assert result["status"] == "failed"
        assert result["code"] == "adept_borrower_profile_unproven"
    finally:
        svc.close()
