"""Core P0 #2055: consented BnF ACSM staging with an exact borrower.

Tests use the actual BnF ProviderSession and HTTPX MockTransport, plus real
Core/SQLite; there is no browser, live account, Adobe runtime or remote I/O.
"""
from dataclasses import replace
from pathlib import Path
import sys

import pytest

from cifereca.models import Loan
from cifereca.plugin_api import ProviderConfigurationError
import cifereca.services as services
from tests.test_sync_state_contract import _service

BNF = Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret"
for directory in (BNF, BNF / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
from bnf_token_fixture import connected


@pytest.fixture()
def connected_core(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session, account, loan, state, calls, downloaded = connected()
    account.identity_id = "default"
    loan.identity_id = "default"
    service.accounts = [account]
    service.storage.provider_connection_put({
        "identity_id": "default", "provider_id": "bnf-pret",
        "credential_id": account.credential_id, "label": "BnF", "enabled": True,
        "metadata": {},
    })
    service._sessions[service._session_key("bnf-pret", account.credential_id)] = session
    monkeypatch.setattr(services, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock")
    try:
        yield service, session, account, loan, state, downloaded
    finally:
        service.close()


def test_consented_exact_loan_stages_candidate_without_ledger_or_adept(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    before_rights = service.storage.entitlements()
    before_acquisitions = service.storage.acquisitions()
    result = service.stage_bnf_loan_acsm(
        loan, confirmed=True, expected_identity_id="default")
    assert result["status"] == "candidate_acsm"
    file = Path(result["path"])
    assert file.read_bytes().startswith(b"<fulfillmentToken")
    assert file.suffix == ".acsm"
    assert len(downloaded) == 1 and downloaded[0].method == "GET"
    assert service.storage.entitlements() == before_rights
    assert service.storage.acquisitions() == before_acquisitions
    assert not session.capabilities.digital_download


def test_no_consent_rejects_before_read_or_local_staging(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    for consent in (False, None, 1, "yes"):
        with pytest.raises(ProviderConfigurationError, match="bnf_explicit_confirmation_required"):
            service.stage_bnf_loan_acsm(
                loan, confirmed=consent, expected_identity_id="default")
    assert not downloaded


def test_identity_scope_never_falls_back_to_selected_profile(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    for candidate, owner in [
        (replace(loan, identity_id="alex"), "alex"),
        (loan, "alex"),
        (replace(loan, source_account_id=None), "default"),
        (replace(loan, source_credential="alien"), "default"),
        (replace(loan, source_provider="paris"), "default"),
        (replace(loan, record_id=""), "default"),
        (replace(loan, state="returned"), "default"),
        (replace(loan, medium="physical"), "default"),
    ]:
        with pytest.raises(ProviderConfigurationError, match="bnf_loan_scope_unproven"):
            service.stage_bnf_loan_acsm(
                candidate, confirmed=True, expected_identity_id=owner)
    assert not downloaded


def test_stale_live_activity_never_stages_even_if_old_right_remains(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    service._persist_active_loans("default", [loan], reconcile_missing=False)
    before = service.storage.entitlements()
    state["activity"] = '<section class="active-loans"></section>'
    with pytest.raises(ProviderConfigurationError, match="bnf_loan_token_unavailable"):
        service.stage_bnf_loan_acsm(loan, confirmed=True, expected_identity_id="default")
    assert before == service.storage.entitlements()
    assert not downloaded and not service.storage.acquisitions()


def test_closed_source_denies_before_network(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    session.connected = False
    with pytest.raises(ProviderConfigurationError, match="bnf_loan_scope_unproven"):
        service.stage_bnf_loan_acsm(loan, confirmed=True, expected_identity_id="default")
    assert not downloaded


def test_identity_switch_before_confirmation_refuses_without_network(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    service.current_identity = service.identity_by_id("alex")
    with pytest.raises(ProviderConfigurationError, match="bnf_loan_scope_unproven"):
        service.stage_bnf_loan_acsm(loan, confirmed=True, expected_identity_id="default")
    assert not downloaded


@pytest.mark.parametrize("payload", [
    (b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
     b'fulfillmentType="buy"/>'),
    (b'<!DOCTYPE token [<!ENTITY hostile "private">]>'
     b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
     b'fulfillmentType="loan"/>'),
    b'<fulfillmentToken fulfillmentType="loan"/>',
])
def test_core_independently_refuses_invalid_loan_claim_even_if_provider_returns_file(
    connected_core, tmp_path, monkeypatch, payload,
):
    service, session, account, loan, state, downloaded = connected_core
    original_rights = service.storage.entitlements()
    original_acquisitions = service.storage.acquisitions()

    def unverified_download(_loan, _account, target_dir, *, explicit_user_action):
        assert explicit_user_action is True
        target_dir.mkdir(parents=True, exist_ok=True)
        staged = target_dir / "unverified.acsm"
        staged.write_bytes(payload)
        return staged, ""

    monkeypatch.setattr(session, "download_digital", unverified_download)
    with pytest.raises(ProviderConfigurationError, match="^bnf_loan_token_invalid$"):
        service.stage_bnf_loan_acsm(
            loan, confirmed=True, expected_identity_id="default",
        )
    assert not downloaded
    assert service.storage.entitlements() == original_rights
    assert service.storage.acquisitions() == original_acquisitions


def test_core_refuses_fallback_to_other_credential_session_before_network(
    connected_core, monkeypatch,
):
    service, session, account, loan, state, downloaded = connected_core
    monkeypatch.setattr(session, "credential_id", "another-credential")
    with pytest.raises(ProviderConfigurationError, match="^bnf_loan_scope_unproven$"):
        service.stage_bnf_loan_acsm(
            loan, confirmed=True, expected_identity_id="default",
        )
    assert not downloaded



@pytest.mark.parametrize("mutation", ["identity", "session", "account"])
def test_source_rebound_during_token_get_disposes_candidate_without_rights(
    connected_core, monkeypatch, mutation,
):
    """The exact session and borrower must survive the network boundary."""
    service, session, account, loan, state, downloaded = connected_core
    before_rights = service.storage.entitlements()
    before_acquisitions = service.storage.acquisitions()
    original_download = session.download_digital
    staged_paths = []

    def change_during_get(*args, **kwargs):
        path, url = original_download(*args, **kwargs)
        staged_paths.append(path)
        if mutation == "identity":
            service.current_identity = service.identity_by_id("alex")
        elif mutation == "session":
            service._sessions[service._session_key(
                "bnf-pret", account.credential_id)] = object()
        else:
            service.accounts = [replace(account)]
        return path, url

    monkeypatch.setattr(session, "download_digital", change_during_get)
    with pytest.raises(ProviderConfigurationError, match="^bnf_loan_scope_unproven$"):
        service.stage_bnf_loan_acsm(
            loan, confirmed=True, expected_identity_id="default")
    assert len(downloaded) == 1
    assert len(staged_paths) == 1
    assert not staged_paths[0].exists()
    assert service.storage.entitlements() == before_rights
    assert service.storage.acquisitions() == before_acquisitions
