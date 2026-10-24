"""Explicit BnF loan-token path, with only synthetic HTTPX traffic."""
from dataclasses import replace
from pathlib import Path
import stat

import pytest

from bnf_token_fixture import connected, TOKEN
from cifereca.plugin_api import ProviderConfigurationError, ProviderError


def test_exact_observed_loan_stages_private_acsm(tmp_path):
    session, account, loan, state, calls, downloaded = connected()
    try:
        path, url = session.download_digital(loan, account, tmp_path / "stage", explicit_user_action=True)
        assert path.read_bytes() == TOKEN
        assert path.suffix == ".acsm" and url == ""
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert len(downloaded) == 1
        assert downloaded[0].method == "GET"
        assert downloaded[0].url.path == "/loans/loan-a"
        assert downloaded[0].url.host == "pret.bnf.fr"
        assert list(path.parent.glob("*.part")) == []
        assert not session.capabilities.borrow
        assert not session.capabilities.digital_download
        assert session.last_loan_errors
    finally:
        session.close()



def test_provider_session_default_download_refused_even_for_valid_loan(tmp_path):
    session, account, loan, _, _, downloaded = connected()
    try:
        from cifereca.plugin_api import ProviderCapabilityError
        with pytest.raises(ProviderCapabilityError, match="bnf_web_explicit_only"):
            session.download_digital(loan, account, tmp_path)
        assert downloaded == []
        assert list(tmp_path.rglob("*.acsm")) == []
    finally:
        session.close()

def test_wrong_identity_source_or_resource_never_fetches_token(tmp_path):
    session, account, loan, _, _, downloaded = connected()
    try:
        wrong = (
            replace(loan, identity_id="foreign"),
            replace(loan, source_credential="foreign"),
            replace(loan, source_account_id="foreign"),
            replace(loan, source_provider="paris"),
            replace(loan, record_id="another-book"),
            replace(loan, id="another-loan"),
        )
        for candidate in wrong:
            with pytest.raises(ProviderConfigurationError, match="loan_not_observed"):
                session.download_digital(candidate, account, tmp_path, explicit_user_action=True)
        assert not downloaded
    finally:
        session.close()


def test_stale_account_page_revokes_download_but_not_persisted_loans(tmp_path):
    session, account, loan, state, _, downloaded = connected()
    try:
        state["activity"] = '<section class="active-loans"></section>'
        with pytest.raises(ProviderError, match="loan_not_observed"):
            session.download_digital(loan, account, tmp_path, explicit_user_action=True)
        assert not downloaded and not list(tmp_path.rglob("*.acsm"))
    finally:
        session.close()


def test_closed_session_never_publishes_inflight_token(tmp_path):
    session, account, loan, _, _, _ = connected()
    previous = session.client.loan_token
    def closing(value):
        token = previous(value)
        session.close()
        return token
    session.client.loan_token = closing
    with pytest.raises(ProviderError, match="session_closed"):
        session.download_digital(loan, account, tmp_path, explicit_user_action=True)
    assert not list(tmp_path.rglob("*.acsm"))
