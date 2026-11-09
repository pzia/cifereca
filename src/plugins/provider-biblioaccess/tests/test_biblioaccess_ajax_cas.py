"""Synthetic-only AJAX + CAS continuation tests. Never contact a provider."""
import importlib.util
from pathlib import Path
import sys
from urllib.parse import quote

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ajax = load("biblioaccess_ajax_logon")
cas = load("biblioaccess_ajax_cas")
QUERY = {"changeUser": "false", "provider": "0", "_dc": "1234567890123"}
FORM = {"username": "SYNTHETIC", "password": "SYNTHETIC",
        "rememberMe": "false", "captcha": "",
        "captchaCheckValue": "SYNTHETIC-CHALLENGE"}
SERVICE = "https://www.biblioaccess.com/1409/"
LOGIN = cas.IDP + "/login?service=" + quote(SERVICE, safe="")
TICKET_RETURN = SERVICE + "?ticket=ST-SYNTHETIC-NOT-REAL"


def client_for(handler):
    return cas.ObservedAjaxCasClient(transport=httpx.MockTransport(handler))


def test_six_step_cas_ajax_remains_unverified():
    seen = []
    def handler(req):
        seen.append((req.method, str(req.url)))
        if req.url.path == "/1409/Account/Login":
            return httpx.Response(302, headers={"location": LOGIN})
        if req.url.path == "/login":
            assert str(req.url) == LOGIN
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text='<html><script src="/assets/login.js?v=SYNTHETIC-OPAQUE"></script>'
                                  '<script>const _dc = 0; const captchaCheckValue = "SYNTHETIC";</script>'
                                  '<input name="username" value="SYNTHETIC-SECRET"></html>')
        if req.url.path == "/logon.svc/logon":
            assert req.method == "POST"
            assert set(req.url.params) == set(QUERY)
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  json={"success": True, "errors": [],
                                        "message": None, "d": "OPAQUE-SYNTHETIC"})
        if req.url.path == "/cas/login":
            assert req.url.params["service"] == SERVICE
            return httpx.Response(302, headers={"location": TICKET_RETURN})
        if str(req.url) == TICKET_RETURN:
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<html>Landing</html>")
        if req.url.path == "/1409/Catalog/MyLoans":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<html>Loans not verified</html>")
        pytest.fail("Unexpected route")
    c = client_for(handler)
    try:
        with pytest.raises(cas.AjaxCasError, match="login_required"):
            c.submit_once(query=QUERY, fields=FORM, consent=True)
        assert not seen
        assert c.observe_entry_once().phase == "entry_redirect_unverified"
        assert c.observe_login_once().phase == "login_html_unverified"
        preflight = c.login_preflight()
        assert preflight.same_origin_script_paths == ("/assets/login.js",)
        assert preflight.inline_identifier_names == ("_dc", "captchaCheckValue")
        assert preflight.field_names == ("username",)
        assert not preflight.challenge_resolved
        assert "SYNTHETIC-SECRET" not in repr(preflight)
        assert c.submit_once(query=QUERY, fields=FORM, consent=True).outcome == "success_reported"
        landing = c.observe_cas_once()
        assert landing.phase == "landing_unverified" and not landing.connected
        loans = c.observe_loans_once()
        assert loans.phase == "loans_html_unverified" and not loans.loans_verified
        assert len(seen) == 6
        assert [m for m, _ in seen] == ["GET", "GET", "POST", "GET", "GET", "GET"]
        assert "ST-SYNTHETIC" not in repr(landing) + repr(loans)
        with pytest.raises(cas.AjaxCasError, match="already_attempted"):
            c.observe_cas_once()
        with pytest.raises(cas.AjaxCasError, match="already_attempted"):
            c.observe_loans_once()
        assert len(seen) == 6
    finally:
        c.close()


@pytest.mark.parametrize("service", [
    "http://www.biblioaccess.com/1409/",
    "https://www.biblioaccess.com/1717/",
    "https://evil.example/1409/",
    "https://www.biblioaccess.com/1409/?token=SYNTHETIC",
])
def test_foreign_cas_services_refused(service):
    destination = cas.IDP + "/login?service=" + quote(service, safe="")
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": destination})
    c = client_for(handler)
    try:
        with pytest.raises(cas.AjaxCasError):
            c.observe_entry_once()
        assert seen == [cas.ENTRY]
    finally:
        c.close()


@pytest.mark.parametrize("target", [
    "https://evil.example/1409/?ticket=ST-FAKE",
    "https://www.biblioaccess.com/1717/?ticket=ST-FAKE",
    "https://www.biblioaccess.com/1409/?ticket=ST-FAKE&token=OTHER",
    "https://www.biblioaccess.com/1409/Catalog/MyLoans?ticket=ST-FAKE",
])
def test_ticket_never_sent_to_foreign_scope(target):
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": target})
    c = client_for(handler)
    c._service = SERVICE
    c._ajax_succeeded = True
    try:
        with pytest.raises(cas.AjaxCasError, match="ticket_refused"):
            c.observe_cas_once()
        assert len(seen) == 1
        assert seen[0].startswith(cas.CAS)
    finally:
        c.close()


def test_failed_ajax_cannot_trigger_cas():
    c = client_for(lambda request: pytest.fail("No requests expected"))
    c._service = SERVICE
    c._ajax_succeeded = False
    try:
        with pytest.raises(cas.AjaxCasError, match="post_not_confirmed"):
            c.observe_cas_once()
    finally:
        c.close()


def test_loans_redirect_not_evidence_and_no_follow():
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": cas.IDP + "/login"})
    c = client_for(handler)
    c._cas_landed = True
    try:
        with pytest.raises(cas.AjaxCasError, match="loans_unverified"):
            c.observe_loans_once()
        assert seen == [cas.LOANS]
    finally:
        c.close()

def test_preflight_not_available_before_login_or_after_close():
    c = client_for(lambda _: pytest.fail("No network"))
    try:
        with pytest.raises(cas.AjaxCasError, match="preflight_unavailable"):
            c.login_preflight()
    finally:
        c.close()
    with pytest.raises(cas.AjaxCasError, match="preflight_unavailable"):
        c.login_preflight()


def test_observed_script_get_is_opt_in_single_no_redirect_and_never_executable():
    seen = []
    def handler(req):
        seen.append((req.method, str(req.url)))
        if req.url.path == "/1409/Account/Login":
            return httpx.Response(302, headers={"location": LOGIN})
        if req.url.path == "/login":
            return httpx.Response(
                200, headers={"content-type": "text/html"},
                text='<script src="/assets/sso.js?cache=PRIVATE-SYNTHETIC"></script>')
        if req.url.path == "/assets/sso.js":
            return httpx.Response(200, headers={"content-type": "application/javascript"},
                text='const _dc="PRIVATE"; const captchaCheckValue="PRIVATE";'
                     ' fetch("/logon.svc/logon")')
        pytest.fail("Unexpected")
    c = client_for(handler)
    try:
        c.observe_entry_once()
        c.observe_login_once()
        with pytest.raises(cas.AjaxCasError, match="consent_required"):
            c.inspect_login_script_once(path="/assets/sso.js", consent=False)
        with pytest.raises(cas.AjaxCasError, match="unobserved"):
            c.inspect_login_script_once(path="/assets/other.js", consent=True)
        assert len(seen) == 2
        result = c.inspect_login_script_once(path="/assets/sso.js", consent=True)
        assert result.identifier_names == ("_dc", "captchaCheckValue")
        assert result.ajax_endpoint_referenced
        assert not result.executable and not result.value_provenance_verified
        assert "PRIVATE" not in repr(result)
        with pytest.raises(cas.AjaxCasError, match="already_attempted"):
            c.inspect_login_script_once(path="/assets/sso.js", consent=True)
        assert len(seen) == 3
        assert seen[2] == ("GET", cas.IDP + "/assets/sso.js?cache=PRIVATE-SYNTHETIC")
    finally:
        c.close()


def test_observed_script_redirect_denied_without_external_get():
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": "https://evil.example/script.js"})
    c = client_for(handler)
    try:
        from biblioaccess_ajax_preflight import LoginPreflight
        c._preflight = LoginPreflight(
            script_count=1, same_origin_script_paths=("/assets/sso.js",),
            external_script_count=1, inline_script_count=0,
            field_names=(), inline_identifier_names=(),
            _script_request_urls=(("/assets/sso.js", cas.IDP + "/assets/sso.js"),))
        with pytest.raises(cas.AjaxCasError, match="script_unavailable"):
            c.inspect_login_script_once(path="/assets/sso.js", consent=True)
        assert seen == [cas.IDP + "/assets/sso.js"]
    finally:
        c.close()


@pytest.mark.parametrize("failure_kind, expected_error", [
    ("redirect", "cas_login_unverified"),
    ("forbidden", "cas_login_unverified"),
    ("bad_content_type", "cas_login_unverified"),
    ("oversized_body", "cas_body_limit"),
    ("bad_encoding", "cas_transport_unavailable"),
    ("invalid_html", "login_preflight_invalid_html"),
    ("transport", "cas_transport_unavailable"),
])
def test_failed_login_get_never_enables_ajax_post(failure_kind, expected_error):
    """Credential POST must be impossible after any failed preflight."""
    seen = []

    def handler(req):
        seen.append((req.method, str(req.url)))
        if req.url.path == "/1409/Account/Login":
            return httpx.Response(302, headers={"location": LOGIN})
        assert req.url.path == "/login"
        if failure_kind == "redirect":
            return httpx.Response(302, headers={"location": cas.IDP + "/another"})
        if failure_kind == "forbidden":
            return httpx.Response(403, headers={"content-type": "text/html"})
        if failure_kind == "bad_content_type":
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  json={"error": "unverified"})
        if failure_kind == "oversized_body":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  content=b"x" * (256 * 1024 + 1))
        if failure_kind == "bad_encoding":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  content=b"\xff")
        if failure_kind == "invalid_html":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text='<script src="a.js" src="b.js"></script>')
        raise httpx.ConnectError("synthetic transport failure", request=req)

    c = client_for(handler)
    try:
        c.observe_entry_once()
        with pytest.raises(ValueError, match=expected_error):
            c.observe_login_once()
        with pytest.raises(cas.AjaxCasError, match="cas_login_required"):
            c.submit_once(query=QUERY, fields=FORM, consent=True)
        with pytest.raises(cas.AjaxCasError, match="cas_preflight_unavailable"):
            c.login_preflight()
        with pytest.raises(cas.AjaxCasError, match="cas_login_unavailable"):
            c.observe_login_once()
        assert len(seen) == 2
        assert [method for method, _ in seen] == ["GET", "GET"]
    finally:
        c.close()


def test_successful_login_get_still_allows_exactly_one_ajax_post():
    seen = []

    def handler(req):
        seen.append(req.method + " " + req.url.path)
        if req.url.path == "/1409/Account/Login":
            return httpx.Response(302, headers={"location": LOGIN})
        if req.url.path == "/login":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<html>Login</html>")
        if req.url.path == "/logon.svc/logon":
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  json={"success": False})
        pytest.fail("Unexpected request")

    c = client_for(handler)
    try:
        c.observe_entry_once()
        c.observe_login_once()
        assert c.submit_once(query=QUERY, fields=FORM, consent=True).outcome == "failure_reported"
        with pytest.raises(ajax.AjaxLogonError, match="already_attempted"):
            c.submit_once(query=QUERY, fields=FORM, consent=True)
        with pytest.raises(cas.AjaxCasError, match="post_not_confirmed"):
            c.observe_cas_once()
        assert seen == ["GET /1409/Account/Login", "GET /login", "POST /logon.svc/logon"]
    finally:
        c.close()

def test_login_click_returnurl_is_sent_before_any_sso_network():
    """Reproduces the observed BiblioAccess 1409 authentication click."""
    from urllib.parse import parse_qs, urlsplit
    assert cas.ENTRY == (
        "https://www.biblioaccess.com/1409/Account/Login"
        "?returnUrl=https%3A%2F%2Fwww.biblioaccess.com%2F1409%2F"
    )
    sent = []
    def handler(req):
        sent.append(str(req.url))
        assert req.method == "GET"
        assert req.url.path == "/1409/Account/Login"
        assert parse_qs(req.url.query.decode()) == {
            "returnUrl": ["https://www.biblioaccess.com/1409/"]
        }
        return httpx.Response(302, headers={"location": LOGIN})
    c = client_for(handler)
    try:
        assert c.observe_entry_once().phase == "entry_redirect_unverified"
        assert sent == [cas.ENTRY]
    finally:
        c.close()


def test_login_click_refuses_empty_cas_service_without_following():
    """Fixing returnUrl must not relax the CAS service guard."""
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(
            302, headers={"location": cas.IDP + "/login?service="})
    c = client_for(handler)
    try:
        with pytest.raises(cas.AjaxCasError, match="cas_url_refused"):
            c.observe_entry_once()
        assert seen == [cas.ENTRY]
        with pytest.raises(cas.AjaxCasError, match="cas_login_unavailable"):
            c.observe_login_once()
    finally:
        c.close()


@pytest.mark.parametrize("ajax_payload", [
    {"success": True, "errors": ["SYNTHETIC-LOGIN-REJECTED"]},
    {"success": True, "errors": {"code": "rejected"}},
    {"success": True, "errors": None},
])
def test_ajax_contradiction_never_triggers_cas_get(ajax_payload):
    calls = []
    def handler(req):
        calls.append(req.method + " " + req.url.path)
        if req.url.path == "/1409/Account/Login":
            return httpx.Response(302, headers={"location": LOGIN})
        if req.url.path == "/login":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<html>Login</html>")
        if req.url.path == "/logon.svc/logon":
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  json=ajax_payload)
        pytest.fail("CAS must not execute after contradictory AJAX result")
    c = client_for(handler)
    try:
        c.observe_entry_once()
        c.observe_login_once()
        result = c.submit_once(query=QUERY, fields=FORM, consent=True)
        assert result.outcome == "unknown"
        with pytest.raises(cas.AjaxCasError, match="cas_post_not_confirmed"):
            c.observe_cas_once()
        assert calls == ["GET /1409/Account/Login", "GET /login",
                         "POST /logon.svc/logon"]
    finally:
        c.close()


def test_exact_script_url_selected_without_query_values_in_diagnostics():
    seen = []
    token = "SYNTHETIC-PRIVATE-CACHE-VALUE"
    def handler(req):
        seen.append(str(req.url))
        if req.url.path == "/1409/Account/Login":
            return httpx.Response(302, headers={"location": LOGIN})
        if req.url.path == "/login":
            return httpx.Response(200, headers={"content-type": "text/html"},
                text='<script src="/scripts/logon.js?version=' + token + '"></script>')
        if req.url.path == "/scripts/logon.js":
            assert req.url.params["version"] == token
            return httpx.Response(200, headers={"content-type": "application/javascript"},
                                  text='const _dc=123;')
        pytest.fail("Unexpected request")
    client = client_for(handler)
    try:
        client.observe_entry_once()
        client.observe_login_once()
        preflight = client.login_preflight()
        assert preflight.same_origin_script_paths == ("/scripts/logon.js",)
        assert token not in repr(preflight)
        evidence = client.inspect_login_script_once(path="/scripts/logon.js", consent=True)
        assert evidence.identifier_names == ("_dc",)
        assert token not in repr(evidence)
        assert seen[-1] == cas.IDP + "/scripts/logon.js?version=" + token
    finally:
        client.close()


def test_ambiguous_observed_script_urls_never_qualify_a_get():
    seen = []
    def handler(req):
        seen.append(str(req.url))
        if req.url.path == "/1409/Account/Login":
            return httpx.Response(302, headers={"location": LOGIN})
        if req.url.path == "/login":
            return httpx.Response(200, headers={"content-type": "text/html"},
                text='<script src="/scripts/logon.js?v=one"></script>'
                     '<script src="/scripts/logon.js?v=two"></script>')
        pytest.fail("Ambiguous script must not be requested")
    client = client_for(handler)
    try:
        client.observe_entry_once()
        client.observe_login_once()
        assert client.login_preflight().same_origin_script_paths == ()
        with pytest.raises(cas.AjaxCasError, match="cas_script_unobserved"):
            client.inspect_login_script_once(path="/scripts/logon.js", consent=True)
        assert len(seen) == 2
    finally:
        client.close()


def test_myloans_http_200_exposes_staged_book_links_but_never_a_loan():
    calls = []
    def handler(req):
        calls.append(req.url.path)
        assert req.url.path == "/1409/Catalog/MyLoans"
        return httpx.Response(
            200, headers={"content-type": "text/html"},
            text='<html><a href="/1409/Catalog/Book/123/a-slug">SYNTHETIC BOOK</a>'
                 '<a href="/1409/Catalog/Book/123/a-slug">dup</a>'
                 '<a href="/1409/Catalog/Book/456">other</a></html>')
    c = client_for(handler)
    c._cas_landed = True  # prior CAS success is simulated, never remote
    try:
        result = c.observe_loans_once()
        assert calls == ["/1409/Catalog/MyLoans"]
        assert result.page_book_link_count == 3
        assert result.page_book_unique_count == 2
        assert result._candidate_book_urls == (
            ("123", "https://www.biblioaccess.com/1409/Catalog/Book/123/a-slug"),
            ("456", "https://www.biblioaccess.com/1409/Catalog/Book/456"))
        assert result.phase == "loans_html_unverified"
        assert not result.loans_verified and not result.identity_verified
        assert not result.inventory_verified
        assert "123" not in repr(result)
        with pytest.raises(cas.AjaxCasError, match="cas_loans_already_attempted"):
            c.observe_loans_once()
    finally:
        c.close()


def test_auto_post_uses_fresh_html_hidden_value_and_epoch_ms():
    from time import time
    seen = []
    token = "SYNTHETIC-HIDDEN-FRESH-NO-REUSE"
    def handler(req):
        seen.append(req.method + " " + req.url.path)
        if req.url.path == "/1409/Account/Login":
            return httpx.Response(302, headers={"location": LOGIN})
        if req.url.path == "/login":
            return httpx.Response(
                200, headers={"content-type": "text/html"},
                text='<div id="logon-captcha-form-group" style="display:none"></div>'
                     '<input id="logon-captcha" name="captcha">'
                     '<input type="hidden" id="logon-captcha-checkvalue" '
                     'name="checkValue" value="' + token + '">'
                     '<script>logonManager.provider = "Form";'
                     'logonManager.action = "logon";</script>')
        if req.url.path == "/logon.svc/logon":
            assert req.url.params["changeUser"] == "true"
            assert req.url.params["provider"] == "Form"
            assert abs(int(req.url.params["_dc"])/1000 - time()) < 15
            from urllib.parse import parse_qs
            posted = parse_qs(req.content.decode(), keep_blank_values=True)
            assert posted == {
                "username": ["SYNTHETIC-USER"],
                "password": ["SYNTHETIC-PASSWORD"],
                "rememberMe": ["false"],
                "captcha": [""],
                "captchaCheckValue": [token],
            }
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  json={"success": False, "errors": []})
        pytest.fail("No CAS after failed AJAX POST")
    c = client_for(handler)
    try:
        c.observe_entry_once()
        c.observe_login_once()
        assert token not in repr(c.login_preflight())
        result = c.submit_observed_login_once(
            username="SYNTHETIC-USER",
            password="SYNTHETIC-PASSWORD", consent=True)
        assert result.outcome == "failure_reported"
        assert seen == [
            "GET /1409/Account/Login", "GET /login",
            "POST /logon.svc/logon"]
        with pytest.raises(ajax.AjaxLogonError, match="ajax_logon_already_attempted"):
            c.submit_observed_login_once(
                username="SYNTHETIC-USER",
                password="SYNTHETIC-PASSWORD", consent=True)
    finally:
        c.close()


def test_auto_post_refuses_unknown_challenge_before_credentials_leave():
    seen = []
    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/1409/Account/Login":
            return httpx.Response(302, headers={"location": LOGIN})
        if req.url.path == "/login":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text='<div id="logon-captcha-form-group" '
                                       'style="display:block"></div>')
        pytest.fail("No credential POST on unqualified challenge")
    c = client_for(handler)
    try:
        c.observe_entry_once()
        c.observe_login_once()
        with pytest.raises(cas.AjaxCasError, match="cas_login_dynamic_inputs_unavailable"):
            c.submit_observed_login_once(
                username="SYNTHETIC", password="SYNTHETIC", consent=True)
        assert seen == ["/1409/Account/Login", "/login"]
    finally:
        c.close()



def test_fresh_auto_post_success_follows_cas_and_reads_myloans_same_cookie_jar():
    """Positive synthetic chain: GET/GET/POST/GET/GET/GET, never Core connected.

    The real provider has not yet demonstrated this path; fixtures contain
    invented sessions, tokens, credentials and a fake loan-like link.
    """
    from urllib.parse import parse_qs
    seen = []
    hidden = "SYNTHETIC-EPHEMERAL-CHECK-VALUE"
    username = "SYNTHETIC-FRESH-READER"
    password = "SYNTHETIC-PRIVATE-PASSWORD"

    def handler(request):
        seen.append((request.method, request.url.path))
        path = request.url.path
        if path == "/1409/Account/Login":
            assert request.method == "GET"
            return httpx.Response(302, headers={"location": LOGIN})
        if path == "/login":
            assert request.method == "GET" and str(request.url) == LOGIN
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/html",
                    "set-cookie": "idp_synthetic=first; Path=/; Secure; HttpOnly",
                },
                text='<div id="logon-captcha-form-group" style="display:none"></div>'
                     '<input id="logon-captcha" name="captcha">'
                     '<input type="hidden" id="logon-captcha-checkvalue" '
                     'name="checkValue" value="' + hidden + '">'
                     "<script>logonManager.provider='Form';"
                     "logonManager.action='logon';</script>",
            )
        if path == "/logon.svc/logon":
            assert request.method == "POST"
            assert "idp_synthetic=first" in request.headers.get("cookie", "")
            assert request.url.params["changeUser"] == "true"
            assert request.url.params["provider"] == "Form"
            assert request.url.params["_dc"].isdigit()
            posted = parse_qs(request.content.decode("ascii"), keep_blank_values=True)
            assert posted == {
                "username": [username], "password": [password],
                "rememberMe": ["false"], "captcha": [""],
                "captchaCheckValue": [hidden],
            }
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "set-cookie": "idp_logged_in=second; Path=/; Secure; HttpOnly",
                },
                json={"errors": [], "message": None, "success": True, "d": None},
            )
        if path == "/cas/login":
            assert request.method == "GET"
            cookie = request.headers.get("cookie", "")
            assert "idp_synthetic=first" in cookie
            assert "idp_logged_in=second" in cookie
            assert request.url.params["service"] == SERVICE
            return httpx.Response(302, headers={"location": TICKET_RETURN})
        if str(request.url) == TICKET_RETURN:
            assert request.method == "GET"
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/html",
                    "set-cookie": "library_session=third; Path=/; Secure; HttpOnly",
                },
                text="<html>Logged-in-looking synthetic landing</html>",
            )
        if path == "/1409/Catalog/MyLoans":
            assert request.method == "GET"
            assert "library_session=third" in request.headers.get("cookie", "")
            return httpx.Response(
                200, headers={"content-type": "text/html"},
                text='<a href="/1409/Catalog/Book/123456/synthetic-notice">Notice</a>',
            )
        pytest.fail("Unexpected network operation")

    client = client_for(handler)
    try:
        client.observe_entry_once()
        client.observe_login_once()
        assert hidden not in repr(client.login_preflight())
        ajax_result = client.submit_observed_login_once(
            username=username, password=password, consent=True)
        assert ajax_result.outcome == "success_reported"
        landing = client.observe_cas_once()
        books = client.observe_loans_once()
        assert books.page_book_link_count == 1
        assert books.page_book_unique_count == 1
        assert not landing.connected and not books.connected
        assert not books.identity_verified and not books.loans_verified
        assert not books.inventory_verified
        assert seen == [
            ("GET", "/1409/Account/Login"), ("GET", "/login"),
            ("POST", "/logon.svc/logon"), ("GET", "/cas/login"),
            ("GET", "/1409/"), ("GET", "/1409/Catalog/MyLoans"),
        ]
        for secret in (hidden, username, password, "ST-SYNTHETIC", "third"):
            assert secret not in repr(ajax_result) + repr(landing) + repr(books)
    finally:
        client.close()


def test_persisted_cas_session_reads_fresh_myloans_without_new_credential_post():
    requests = []
    html = (
        '<form id="logoutForm" method="post" action="/1409/Account/LogOff"></form>'
        '<div class="row JSProductRow-10001">'
        '<a href="/1409/Catalog/Book/20002/a-book">book</a>'
        '<p class="titleLoans">SYNTHETIC-BOOK</p>'
        '<span class="jsDateEnd">08/11/2026 12:30:00</span></div>'
    )
    def handler(request):
        requests.append((request.method, request.url.path,
                         request.headers.get("cookie", "")))
        assert request.method == "GET"
        assert request.url.path == "/1409/Catalog/MyLoans"
        assert "synthetic_library=SESSION" in request.headers.get("cookie", "")
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text=html)
    client = client_for(handler)
    client.http.cookies.set(
        "synthetic_library", "SESSION", domain="www.biblioaccess.com", path="/1409")
    client._ajax_succeeded = True
    client._cas_landed = True
    client._loans_attempted = True  # initial POST + CAS + MyLoans completed
    try:
        a = client.read_authenticated_loans()
        b = client.read_authenticated_loans()
        assert a.page_loan_row_count == b.page_loan_row_count == 1
        assert a.logout_form_observed and b.logout_form_observed
        assert not a.identity_verified and not a.inventory_verified
        assert [method for method, _, _ in requests] == ["GET", "GET"]
        assert "SESSION" not in repr(a) + repr(b)
        assert "SYNTHETIC-BOOK" not in repr(a) + repr(b)
    finally:
        client.close()


def test_authenticated_session_refuses_login_page_downgrade_even_on_http_200():
    requests = []
    def handler(request):
        requests.append(request.method)
        return httpx.Response(
            200, headers={"content-type": "text/html"},
            text='<html><a href="/1409/Catalog/Book/123/teaser">public</a></html>')
    client = client_for(handler)
    client._ajax_succeeded = True
    client._cas_landed = True
    client._loans_attempted = True
    try:
        with pytest.raises(cas.AjaxCasError, match="cas_authenticated_page_unverified"):
            client.read_authenticated_loans()
        assert requests == ["GET"]
    finally:
        client.close()


def test_authenticated_session_never_follows_expiry_redirect():
    requests = []
    def handler(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(
            302, headers={"location": "https://mediatheques.agglo-larochelle.fr/login"})
    client = client_for(handler)
    client._ajax_succeeded = True
    client._cas_landed = True
    client._loans_attempted = True
    try:
        with pytest.raises(cas.AjaxCasError, match="cas_loans_unverified"):
            client.read_authenticated_loans()
        assert requests == [("GET", "/1409/Catalog/MyLoans")]
    finally:
        client.close()


def test_duplicate_product_reference_revokes_fresh_myloans_observation():
    """No first-wins loan after a repeated product row in private MyLoans."""
    requests = []
    def row(ref, book):
        return (
            '<div class="row JSProductRow-' + ref + '">'
            '<a href="/1409/Catalog/Book/' + book + '/example">notice</a>'
            '<p class="titleLoans">SYNTHETIC PRIVATE TITLE</p>'
            '<span class="jsDateEnd">08/11/2026 12:30:00</span></div>'
        )
    body = (
        '<form id="logoutForm" method="post" '
        'action="/1409/Account/LogOff"></form>'
        + row("10001", "20002") + row("10001", "30003")
    )
    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET"
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)
    client = client_for(handler)
    client._ajax_succeeded = True
    client._cas_landed = True
    client._loans_attempted = True
    try:
        with pytest.raises(cas.AjaxCasError, match="cas_loans_structure_unverified"):
            client.read_authenticated_loans()
        assert requests == [("GET", "/1409/Catalog/MyLoans")]
    finally:
        client.close()
