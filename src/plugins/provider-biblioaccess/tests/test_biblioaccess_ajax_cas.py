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
        assert seen[2] == ("GET", cas.IDP + "/assets/sso.js")
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
            field_names=(), inline_identifier_names=())
        with pytest.raises(cas.AjaxCasError, match="script_unavailable"):
            c.inspect_login_script_once(path="/assets/sso.js", consent=True)
        assert seen == [cas.IDP + "/assets/sso.js"]
    finally:
        c.close()
