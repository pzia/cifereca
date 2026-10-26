"""#1884/#2024: synthetic HTTPX cookies and recent login, no browser/live claim."""
import json
import logging
from time import monotonic
import http.cookiejar

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_delivery_trace import DeliveryTrace, destination_category
from tests.test_numilog_delivery_observability_p1 import Capture, assert_private_data_absent
from tests.test_numilog_session_state_diagnostics_p0 import session_for, SECRET

READ = '/Pages/Compte/readBook.ashx'
LIBRARY = '/Pages/Compte/MesLivres.aspx'
LOGIN = '/Login.Aspx'
HTML = ('<table id="myTable"><div><span class="pTitle">Synthetic</span>'
        '<a class="LnkDownloadBook" href="' + READ + '?lid=12&amp;m=download">Download</a>'
        '</div></table>')


@pytest.mark.parametrize('attributes,sent', [
    ('Path=/; Secure; SameSite=Lax', True),
    ('Domain=.numilog.com; Path=/; Secure; SameSite=Strict', True),
    ('Path=/Pages/Compte; Secure; SameSite=None', True),
    ('Path=/Other; Secure', False),
    ('Domain=foreign.invalid; Path=/; Secure', False),
    ('Path=/; Max-Age=0; Secure', False),
])
def test_recent_success_then_login_form_reports_evidence_not_expiry(tmp_path, caplog, attributes, sent):
    requests = []
    diag = Capture()
    def handler(request):
        requests.append(request)
        path = request.url.path
        if path == '/':
            return httpx.Response(200, text='Home')
        if path == '/Login.Aspx/AuthUser':
            return httpx.Response(200, json={'d': {'AuthSuccess': True}},
                headers={'set-cookie': 'fixture='+SECRET+'; '+attributes})
        if path == LIBRARY:
            return httpx.Response(200, text=HTML)
        if path == READ:
            return httpx.Response(302, headers={'location': LOGIN+'?auth='+SECRET,
                                               'content-type': 'text/html'})
        assert path == LOGIN
        return httpx.Response(200, text='<form><input type="password" value="PRIVATE_HTML"></form>',
                              headers={'content-type': 'text/html'})
    caplog.set_level(logging.INFO, logger='cifereca.plugin.provider.numilog')
    session = session_for(handler, diagnostics=diag)
    try:
        started = monotonic()
        session.connect()
        assert session.connected is True
        item = session.library()[0]
        with pytest.raises(ProviderError, match='formulaire de connexion') as error:
            session.download_direct(item, tmp_path)
        assert monotonic()-started < 60
        assert 'expir' not in str(error.value)
        assert session.connected is False
        assert [r.url.path for r in requests] == [
            '/', '/Login.Aspx/AuthUser', LIBRARY, LIBRARY, LIBRARY, READ, LOGIN]
        read_request = next(r for r in requests if r.url.path == READ)
        assert read_request.headers.get('referer') == session.library_url
        assert requests[-1].headers.get('referer') == session.base_url
        assert ('fixture='+SECRET in read_request.headers.get('cookie', '')) is sent
        expected_redirect_cookie = sent and 'Path=/Pages/Compte' not in attributes
        assert ('fixture='+SECRET in requests[-1].headers.get('cookie', '')) is expected_redirect_cookie
        assert list(tmp_path.iterdir()) == []
        events = diag.acquisition()
        assert len(events) == 5
        assert len({kw['detail']['action'] for _, kw in events}) == 1
        assert all(kw['detail']['connected_initial'] is True for _, kw in events)
        details = [kw['detail'] for _, kw in events]
        assert [(d['http_status'], d['path_kind'], d['cookie_header_present'])
                for d in details if 'http_status' in d] == [
            (302, 'readbook', sent),
            (200, 'login_page', expected_redirect_cookie)]
        # Proven from the 302 Location after the existing origin check,
        # never from a stored/signed Location string or presumed expiry.
        redirect = next(d for d in details if d.get('http_status') == 302)
        assert redirect['redirect_target_kind'] == 'login_page'
        assert redirect['redirect_target_origin_kind'] == 'numilog_site'
        assert 'redirect_target_kind' not in next(
            d for d in details if d.get('http_status') == 200)
        assert details[-2]['code'] == details[-1]['code'] == 'login_form_detected'
        assert details[-2]['html_kind'] == 'login' and details[-1]['hops'] == 2
        assert events[-1][0][2] == 'error'
        assert 0 <= events[-1][1]['duration_ms'] <= 600000
        assert 'connected=True reason=connect_succeeded' in caplog.text
        assert 'connected=False reason=delivery_login_form_detected' in caplog.text
        exported = json.dumps(diag.events)+caplog.text+str(error.value)
        assert SECRET not in exported and 'auth=' not in exported
        assert 'session_expired_confirmed' not in exported
        assert_private_data_absent(diag)
        count = len(requests)
        with pytest.raises(ProviderError, match='connecter le compte'):
            session.download_direct(item, tmp_path)
        assert len(requests) == count
        session.connect()  # only an explicit reconnect permits another attempt
        assert session.connected is True
        with pytest.raises(ProviderError, match='formulaire de connexion'):
            session.download_direct(item, tmp_path)
        assert len(diag.completed()) == 2
        assert len({kw['detail']['action'] for _, kw in diag.completed()}) == 2
        assert session.connected is False
    finally:
        session.close()


def test_secure_cookie_never_sent_on_http_loopback():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200)
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        client.cookies.jar.set_cookie(http.cookiejar.Cookie(
            0, 'fixture', SECRET, None, False, '127.0.0.1', False, False,
            '/', True, True, None, True, None, None, {'SameSite': 'Strict'}, False))
        client.get('http://127.0.0.1/Pages/Compte/readBook.ashx')
        assert 'cookie' not in requests[0].headers
        # SameSite is retained; HTTPX has no browser navigation context to enforce it.
        assert next(iter(client.cookies.jar)).get_nonstandard_attr('SameSite') == 'Strict'


@pytest.mark.parametrize('value', ['PRIVATE_ACCOUNT', 1, {}, None])
def test_initial_connection_evidence_requires_exact_boolean(value):
    diag = Capture()
    with DeliveryTrace(diag, connected_initial=value):
        pass
    assert all('connected_initial' not in kw['detail'] for _, kw in diag.acquisition())
    assert_private_data_absent(diag)


def test_only_exact_numilog_login_paths_get_a_public_login_category():
    # Query strings may contain signed/private values; no value is exported.
    assert destination_category('https://www.numilog.com/Login.Aspx?auth='+SECRET) == (
        'numilog_site', 'login_page')
    assert destination_category('https://www.numilog.com/Login.Aspx/AuthUser') == (
        'numilog_site', 'login_page')
    assert destination_category('https://www.numilog.com/Login.Aspx/private') == (
        'numilog_site', 'other')
    assert destination_category('https://other.invalid/Login.Aspx') == (
        'other', 'other')



@pytest.mark.parametrize('final_path', [
    READ, '/Pages/Compte/Landing.aspx',
])
def test_login_widget_off_login_route_keeps_session_but_never_follows_link(
    tmp_path, caplog, final_path,
):
    """A password input on a site template does not prove session expiry.

    Even a signed-looking ACSM link must *not* be followed while the HTML
    contains an auth form: this is a diagnostic distinction, not a bypass.
    """
    from tests.test_entrepot2r_fulfillment_mock_p0 import SIGNED
    requests = []
    diag = Capture()

    def handler(request):
        requests.append(request)
        if request.url.path == '/' :
            return httpx.Response(200, text='Home')
        if request.url.path == '/Login.Aspx/AuthUser':
            return httpx.Response(200, json={'d': {'AuthSuccess': True}})
        if request.url.path == LIBRARY:
            return httpx.Response(200, text=HTML)
        if request.url.path == READ:
            if final_path == READ:
                return httpx.Response(200, headers={'content-type': 'text/html'},
                    text='<input type="password" />'
                         + '<a href="' + SIGNED + '">Synthetic delivery</a>')
            return httpx.Response(302, headers={'location': final_path})
        assert request.url.path == final_path
        return httpx.Response(200, headers={'content-type': 'text/html'},
            text='<div>Public page template</div><input type="password" />'
                 + '<a href="' + SIGNED + '">Synthetic delivery</a>')

    caplog.set_level(logging.INFO, logger='cifereca.plugin.provider.numilog')
    session = session_for(handler, diagnostics=diag)
    try:
        session.connect()
        item = session.library()[0]
        with pytest.raises(ProviderError, match='session non invalidée') as error:
            session.download_direct(item, tmp_path)
        assert session.connected is True
        assert 'expir' not in str(error.value)
        assert 'delivery_login_form_detected' not in caplog.text
        assert [r.url.path for r in requests] == (
            ['/', '/Login.Aspx/AuthUser', LIBRARY, LIBRARY, LIBRARY, READ]
            + ([] if final_path == READ else [final_path])
        )
        assert all(str(r.url) != SIGNED for r in requests)
        assert list(tmp_path.iterdir()) == []
        result = diag.completed()[0][1]['detail']
        assert result['code'] == 'login_form_detected'
        if final_path != READ:
            redirect = next(kw['detail'] for _, kw in diag.acquisition()
                            if kw['detail'].get('http_status') == 302)
            assert redirect['redirect_target_kind'] == 'other'
            assert redirect['redirect_target_origin_kind'] == 'numilog_site'
        assert result['connected_initial'] is True
        emitted = json.dumps(diag.events) + caplog.text + str(error.value)
        assert SIGNED not in emitted and SECRET not in emitted
        assert_private_data_absent(diag)
    finally:
        session.close()


def test_unrelated_host_login_path_does_not_deauthenticate_numilog_session(tmp_path):
    diag = Capture()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, headers={'content-type': 'text/html'},
                              text='<input type="password"/>')

    session = session_for(handler, diagnostics=diag)
    session.connected = True
    try:
        with pytest.raises(ProviderError, match='session non invalidée'):
            session._download(
                'https://services.numilog.com/Login.Aspx',
                tmp_path / 'target.epub',
            )
        assert len(calls) == 1 and session.connected is True
        assert diag.completed()[0][1]['detail']['code'] == 'login_form_detected'
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()
