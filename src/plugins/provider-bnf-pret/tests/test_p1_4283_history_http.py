"""MockTransport only: explicit history qualification, never public history."""
import httpx
import pytest

from cifereca.plugin_api import ProviderConfigurationError, ProviderError
from cifereca_bnf_pret import BnfProviderSession
from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError, HISTORY, allowed_url
from test_p1_4283_history_events import page, card
from test_http_provider import ACT


def fixture(response=None, after_read=None):
    calls = []
    def handler(request):
        calls.append((request.method, str(request.url)))
        if str(request.url) == HISTORY:
            assert request.headers.get("cookie") == "reader=synthetic"
            if after_read:
                after_read(session, account)
            return response or httpx.Response(200, text=page(card()), headers={"content-type": "text/html"})
        raise AssertionError("unexpected request")
    client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
    session = BnfProviderSession(client=client, credential_id="credential-a", label="BnF",
                                 credentials={"username": "reader", "password": "synthetic"}, options={})
    def login(*args):
        client.http.cookies.set("reader", "synthetic", domain="pret.bnf.fr", path="/")
        return ACT
    client.login = login
    account, = session.connect()
    account.identity_id = "identity-a"
    return session, account, calls


def test_one_scoped_get_only_when_explicitly_requested():
    session, account, calls = fixture()
    try:
        assert not session.capabilities.history
        assert not calls
        result = session._read_history_events(account)
        assert calls == [("GET", HISTORY)]
        assert result.scope.identity_id == "identity-a"
        assert result.scope.account_id == account.unique_id
        assert len(result.events) == 1 and not result.exhaustive
        with pytest.raises(ReaderHTTPError):
            allowed_url(HISTORY)
    finally:
        session.close()


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://authentification.bnf.fr/login"}),
    httpx.Response(302, headers={"location": HISTORY}),
    httpx.Response(401), httpx.Response(403), httpx.Response(429), httpx.Response(503),
    httpx.Response(200, text="<input type='password'>", headers={"content-type": "text/html"}),
    httpx.Response(200, text="x", headers={"content-type": "application/xml"}),
    httpx.Response(200, content=b"x" * (512 * 1024 + 1), headers={"content-type": "text/html"}),
])
def test_failure_is_not_empty_no_redirect_or_retry(response):
    session, account, calls = fixture(response)
    try:
        with pytest.raises(ProviderError):
            session._read_history_events(account)
        assert calls == [("GET", HISTORY)]
    finally:
        session.close()


@pytest.mark.parametrize("change", ["identity", "epoch", "credential", "primary"])
def test_inflight_owner_or_session_change_discards_result(change):
    def mutate(session, account):
        if change == "identity":
            account.identity_id = "other"
        elif change == "epoch":
            session._epoch = "new-epoch"
        elif change == "credential":
            account.credential_id = "other"
        else:
            account.is_primary = False
    session, account, calls = fixture(after_read=mutate)
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session._read_history_events(account)
        assert calls == [("GET", HISTORY)]
    finally:
        session.close()


def test_other_account_and_reconnect_do_not_reuse_old_events():
    first, account, calls = fixture()
    second, other, other_calls = fixture()
    try:
        with pytest.raises(ProviderConfigurationError):
            first._read_history_events(other)
        assert not calls and not other_calls
        fresh, = first.connect()
        fresh.identity_id = "identity-a"
        with pytest.raises(ProviderConfigurationError):
            first._read_history_events(account)
        assert not calls
        assert first._read_history_events(fresh).scope.session_epoch == first._epoch
        assert calls == [("GET", HISTORY)]
    finally:
        first.close()
        second.close()
