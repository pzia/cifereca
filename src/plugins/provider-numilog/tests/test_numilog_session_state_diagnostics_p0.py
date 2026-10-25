"""#1884: explain session loss before delivery without exposing private data."""
import logging

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog import NumilogSession

SECRET = 'PRIVATE_FIXTURE_MARKER'


def session_for(handler, diagnostics=None):
    session = NumilogSession(
        credential_id=SECRET, label=SECRET,
        credentials={'email': SECRET + '@example.test', 'password': SECRET},
        options={}, config={}, diagnostics=diagnostics,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return session


def test_successful_login_then_library_redirect_explains_disconnection(caplog):
    seen = []
    library_reads = 0
    def handler(request):
        nonlocal library_reads
        seen.append(request.url.path)
        if request.url.path == '/Login.Aspx/AuthUser':
            return httpx.Response(200, json={'d': {'AuthSuccess': True}})
        if request.url.path == '/Pages/Compte/MesLivres.aspx':
            library_reads += 1
            if library_reads == 1:
                return httpx.Response(200, text='<table id="myTable"></table>')
            return httpx.Response(302, headers={'location': '/Login.Aspx?auth=' + SECRET})
        return httpx.Response(200, text=SECRET)
    caplog.set_level(logging.INFO, logger='cifereca.plugin.provider.numilog')
    session = session_for(handler)
    try:
        assert len(session.connect()) == 1 and session.connected is True
        with pytest.raises(ProviderError, match='session expirée'):
            session.library()
        assert session.connected is False
        assert 'connected=True reason=connect_succeeded' in caplog.text
        assert 'connected=False reason=library_redirect' in caplog.text
        requests_before = len(seen)
        with pytest.raises(ProviderError, match='connecter le compte'):
            session.library()
        assert len(seen) == requests_before  # no implicit authentication retry
        assert 'reason=not_connected' in caplog.text
        assert SECRET not in caplog.text and 'https://' not in caplog.text
        assert 'AuthUser' not in caplog.text and 'auth=' not in caplog.text
    finally:
        session.close()
    assert 'connected=False reason=session_closed' in caplog.text


@pytest.mark.parametrize('failure', ['structure', 'transport', 'forbidden'])
def test_library_failure_does_not_invent_a_disconnected_session(caplog, failure):
    def handler(request):
        if failure == 'transport':
            raise httpx.ReadTimeout(SECRET + str(request.url), request=request)
        if failure == 'forbidden':
            return httpx.Response(403, text=SECRET)
        return httpx.Response(200, text='<html>' + SECRET + '</html>')
    caplog.set_level(logging.INFO, logger='cifereca.plugin.provider.numilog')
    session = session_for(handler)
    session.connected = True
    try:
        with pytest.raises(ProviderError):
            session.library()
        assert session.connected is True
        assert 'library_redirect' not in caplog.text
        if failure != 'forbidden':
            assert 'library_invalid' in caplog.text if failure == 'structure' else 'library_http_error' in caplog.text
        assert SECRET not in caplog.text
    finally:
        session.close()


def test_authentication_failure_logs_only_fixed_state(caplog):
    def handler(request):
        if request.method == 'POST':
            return httpx.Response(200, json={'d': {'AuthSuccess': False, 'error': SECRET}})
        return httpx.Response(200, text=SECRET)
    caplog.set_level(logging.INFO, logger='cifereca.plugin.provider.numilog')
    session = session_for(handler)
    try:
        with pytest.raises(ProviderError, match='connexion refusée'):
            session.connect()
        assert session.connected is False and not session.accounts
        assert 'reason=connect_failed' in caplog.text
        assert SECRET not in caplog.text
    finally:
        session.close()


def test_diagnostics_and_log_handlers_cannot_break_session_state(monkeypatch, caplog):
    class Broken:
        def add(self, *args, **kwargs):
            raise RuntimeError(SECRET)
    session = session_for(lambda request: httpx.Response(200), diagnostics=Broken())
    try:
        caplog.set_level(logging.INFO, logger='cifereca.plugin.provider.numilog')
        session._session_event(SECRET)
        assert 'reason=unknown' in caplog.text and SECRET not in caplog.text
        monkeypatch.setattr('cifereca_numilog._LOG.log', lambda *a, **k: (_ for _ in ()).throw(RuntimeError(SECRET)))
        session.close()
        assert session.connected is False and not session.credentials
    finally:
        session.close()


def test_numilog_module_logger_propagates_to_the_single_core_journal():
    from cifereca_numilog import _LOG

    assert _LOG.name == "cifereca.plugin.provider.numilog"
    assert _LOG.propagate is True
    assert not _LOG.handlers  # Core owns the one journal handler.
