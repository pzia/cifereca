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
        assert downloaded[0].url.params['device_type'] == 'reader'
        assert downloaded[0].url.params['drm'] == 'acs4'
        assert downloaded[0].url.params['token'] == '0123456789abcdef0123456789abcdef01234567'
        assert list(path.parent.glob("*.part")) == []
        assert not session.capabilities.borrow
        assert not session.capabilities.digital_download
        assert 'last_loan_errors' not in vars(session)
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
    def closing(value, url):
        token = previous(value, url)
        session.close()
        return token
    session.client.loan_token = closing
    with pytest.raises(ProviderError, match="session_closed"):
        session.download_digital(loan, account, tmp_path, explicit_user_action=True)
    assert not list(tmp_path.rglob("*.acsm"))


def test_unsigned_activity_loan_link_is_rejected_without_network(tmp_path):
    """The old reconstructed /loans/{id} path must never be requested."""
    from test_http_provider import ACT, FAKE_SIGNED_HREF
    session, account, loan, state, _, downloaded = connected()
    try:
        state['activity'] = ACT.replace(FAKE_SIGNED_HREF, '/loans/loan-a')
        with pytest.raises(ProviderError, match='^url_refused$'):
            session.download_digital(loan, account, tmp_path,
                                     explicit_user_action=True)
        assert downloaded == [] and not list(tmp_path.rglob('*.acsm'))
    finally:
        session.close()


@pytest.mark.parametrize('wrong', [
    '/loans/loan-b?device_type=reader&drm=acs4&nonce=syntheticNonce123&timestamp=2026-10-26T00%3A00%3A00Z&token=0123456789abcdef0123456789abcdef01234567',
    'https://evil.invalid/loans/loan-a?device_type=reader&drm=acs4&nonce=syntheticNonce123&timestamp=2026-10-26T00%3A00%3A00Z&token=0123456789abcdef0123456789abcdef01234567',
    '/loans/loan-a?device_type=reader&drm=acs4&nonce=syntheticNonce123&timestamp=2026-10-26T00%3A00%3A00Z&token=0123456789abcdef0123456789abcdef01234567&unexpected=x',
    '/loans/loan-a?device_type=reader&drm=acs4&nonce=syntheticNonce123&timestamp=2026-10-26T00%3A00%3A00Z&token=0123456789abcdef0123456789abcdef01234567&token=duplicate',
    '/loans/loan-a?device_type=reader&drm=acs4&nonce=syntheticNonce123&timestamp=2026-10-26T00%3A00%3A00Z',
    '/loans/loan-a',
])
def test_signed_link_parser_refuses_foreign_loan_and_malformed_query(wrong):
    from cifereca_bnf_pret_signed_action import signed_loan_url
    assert signed_loan_url(wrong, 'loan-a') is None


def test_signed_link_in_fresh_activity_is_not_exported_to_core():
    from dataclasses import asdict
    from test_http_provider import ACT
    from cifereca_bnf_pret_web import parse_loans, ProfileScope, ORIGIN
    scope = ProfileScope('profile-a', 'credential-a', 'synthetic-epoch')
    snapshot = parse_loans(ACT, page_url=ORIGIN + '/my_profile/activity',
                           expected=scope, observed=scope)
    assert len(snapshot.loans) == 1
    assert 'token=' not in repr(snapshot)
    assert 'nonce=' not in repr(asdict(snapshot))


class CoreOwnerBroker:
    def __init__(self, account_id, identity_id):
        self.account_id = account_id
        self.identity_id = identity_id
        self.calls = 0

    def accounts(self, identity_id=None):
        from types import SimpleNamespace as NS
        self.calls += 1
        if identity_id is not None and identity_id != self.identity_id:
            return []
        return [NS(provider_id="bnf-pret", account_id=self.account_id,
                   identity_id=self.identity_id)]


def test_explicit_bnf_token_refused_before_fresh_activity_when_core_owner_wrong(tmp_path):
    session, account, loan, state, calls, downloaded = connected()
    session.identity_broker = CoreOwnerBroker(account.unique_id, "foreign-identity")
    before = len(calls)
    try:
        with pytest.raises(ProviderConfigurationError, match="loan_not_observed"):
            session.download_digital(loan, account, tmp_path,
                                     explicit_user_action=True)
        assert len(calls) == before
        assert downloaded == [] and list(tmp_path.rglob("*.acsm")) == []
    finally:
        session.close()


@pytest.mark.parametrize("race", [
    "reassigned_during_activity", "broker_swapped_during_activity",
    "reassigned_during_token",
])
def test_explicit_bnf_token_owner_rebind_never_publishes_acsm(tmp_path, race):
    session, account, loan, _state, _calls, downloaded = connected()
    broker = CoreOwnerBroker(account.unique_id, account.identity_id)
    session.identity_broker = broker
    original_activity = session.client.activity
    original_token = session.client.loan_token

    def activity():
        html = original_activity()
        if race == "reassigned_during_activity":
            broker.identity_id = "another-identity"
        elif race == "broker_swapped_during_activity":
            session.identity_broker = CoreOwnerBroker(
                account.unique_id, account.identity_id,
            )  # same owner, new broker instance A->A
        return html

    def token(loan_id, url):
        value = original_token(loan_id, url)
        if race == "reassigned_during_token":
            broker.identity_id = "another-identity"
        return value

    session.client.activity = activity
    session.client.loan_token = token
    try:
        with pytest.raises(ProviderError, match="loan_not_observed"):
            session.download_digital(loan, account, tmp_path,
                                     explicit_user_action=True)
        assert len(downloaded) == (1 if race == "reassigned_during_token" else 0)
        assert list(tmp_path.rglob("*.acsm")) == []
        assert list(tmp_path.rglob("*.part")) == []
    finally:
        session.close()


def test_stable_core_owner_does_not_change_explicit_bnf_token_result(tmp_path):
    session, account, loan, _state, _calls, downloaded = connected()
    broker = CoreOwnerBroker(account.unique_id, account.identity_id)
    session.identity_broker = broker
    try:
        path, url = session.download_digital(
            loan, account, tmp_path, explicit_user_action=True,
        )
        assert path.read_bytes() == TOKEN
        assert url == ""
        assert len(downloaded) == 1
        assert broker.calls >= 2
    finally:
        session.close()
