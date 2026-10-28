"""P0 #3252: an already-observed DILICOM loan cannot acquire as another borrower.

Only synthetic Paris sessions, public API Account/Loan models and fake HTTP
responses. No real signed token, authentication, Adobe DRM, or remote request.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

import cifereca_paris
from cifereca.plugin_api import ProviderConfigurationError
from cifereca_paris_normalize import normalize_loan
from tests.test_p1_3252_rights_observation_snapshot import fixture


SIGNED = "https://acs4.cantook.net/fictional/loan.acsm?synthetic=1"


def loan_for(account):
    loan = normalize_loan(
        {"Id": "loan-1", "RecordId": "edition-1", "Title": "Synthetic",
         "AdditionalProperties": {"EbookUrl": SIGNED}},
        "DILICOM",
    )
    loan.source_provider = "paris"
    loan.source_credential = account.credential_id
    loan.source_account_id = account.unique_id or "primary"
    loan.identity_id = account.identity_id
    return loan


def prepare(tmp_path, monkeypatch):
    session, account, client = fixture()
    loan = loan_for(account)
    observed = []

    def get(url):
        observed.append(url)
        return NS(
            status_code=200, url=url,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7 SYNTHETIC ONLY",
            raise_for_status=lambda: None,
        )

    client._validate_pnb_redirect_url = lambda url: None
    client._get_pnb_response = get
    client._pnb_trace_response = lambda *_args, **_kwargs: None
    monkeypatch.setattr(
        cifereca_paris, "inspect_artifact_content",
        lambda *_args, **_kwargs: NS(valid=True),
    )
    monkeypatch.setattr(
        cifereca_paris, "publish_artifact_content",
        lambda path, data: path.write_bytes(data),
    )
    return session, account, loan, client, observed


def test_paris_download_valid_exact_core_account_copy_stages_only_own_loan(tmp_path, monkeypatch):
    session, account, loan, _client, requested = prepare(tmp_path, monkeypatch)
    try:
        delivered, url = session.download_digital(loan, replace(account), tmp_path)
        assert delivered.exists()
        assert delivered.read_bytes().startswith(b"%PDF-")
        assert url == SIGNED
        assert requested == [SIGNED]
        assert delivered.suffix == ".pdf"
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    "foreign_identity", "foreign_credential", "foreign_account",
    "foreign_loan_account", "foreign_loan_credential", "physical_loan",
    "unlinked_broker", "ambiguous_broker", "replaced_account",
])
def test_paris_foreign_loan_or_borrower_refused_before_signed_get(tmp_path, monkeypatch, change):
    session, account, loan, client, requested = prepare(tmp_path, monkeypatch)
    try:
        if change == "foreign_identity":
            account.identity_id = "identity-B"
        elif change == "foreign_credential":
            account.credential_id = "other-credential"
        elif change == "foreign_account":
            account.unique_id = "other-borrower"
        elif change == "foreign_loan_account":
            loan.source_account_id = "other-borrower"
        elif change == "foreign_loan_credential":
            loan.source_credential = "other-credential"
        elif change == "physical_loan":
            loan.medium = "physical"
        elif change == "unlinked_broker":
            session.identity_broker.identities = ("identity-B",)
        elif change == "ambiguous_broker":
            session.identity_broker.identities = ("identity-A", "identity-A")
        else:
            session._accounts = [replace(account)]
        with pytest.raises(ProviderConfigurationError, match="paris_download_scope_mismatch"):
            session.download_digital(loan, account, tmp_path)
        assert requested == []
        assert client.calls == []
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


@pytest.mark.parametrize("race", [
    "mutable_caller", "mutable_loan", "broker_swap_same_owner",
    "broker_rebind", "live_account_aba",
])
def test_paris_scope_rebind_during_signed_get_refuses_file_publication(
    tmp_path, monkeypatch, race,
):
    session, account, loan, client, requested = prepare(tmp_path, monkeypatch)
    original = client._get_pnb_response

    def during_get(url):
        response = original(url)
        if race == "mutable_caller":
            account.identity_id = "identity-B"
        elif race == "mutable_loan":
            loan.source_account_id = "reader-B"
        elif race == "broker_swap_same_owner":
            session.identity_broker = type(session.identity_broker)()
        elif race == "broker_rebind":
            session.identity_broker.identities = ("identity-B",)
        else:
            session._accounts[0] = replace(account)
        return response

    client._get_pnb_response = during_get
    try:
        with pytest.raises(ProviderConfigurationError, match="paris_download_scope_mismatch"):
            session.download_digital(loan, account, tmp_path)
        assert requested == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_paris_disconnect_during_pnb_response_never_stages_file(tmp_path, monkeypatch):
    session, account, loan, client, requested = prepare(tmp_path, monkeypatch)
    original = client._get_pnb_response

    def during_get(url):
        response = original(url)
        session.connected = False
        return response

    client._get_pnb_response = during_get
    try:
        with pytest.raises(ProviderConfigurationError, match="paris_download_scope_mismatch"):
            session.download_digital(loan, account, tmp_path)
        assert requested == [SIGNED]
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()
