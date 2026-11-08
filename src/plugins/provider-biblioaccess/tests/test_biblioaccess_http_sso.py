"""Synthetic-only HTTP/CAS and POST shape contracts, without a browser."""
import importlib.util
from pathlib import Path
import sys
import pytest

PATH = Path(__file__).resolve().parents[1] / "biblioaccess_http_sso.py"
spec = importlib.util.spec_from_file_location("biblioaccess_http_sso", PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


@pytest.mark.parametrize(("method", "url", "status", "location", "expected"), [
    ("GET", "https://mediatheques.agglo-larochelle.fr/login", 200, "", "login_form_unverified"),
    ("POST", "https://mediatheques.agglo-larochelle.fr/logon", 200, "", "logon_response_unverified"),
    ("GET", "https://www.biblioaccess.com/1409/Catalog/MyLoans", 200, "", "loans_html_unverified"),
    ("GET", "https://mediatheques.agglo-larochelle.fr/cas/login", 302,
     "https://www.biblioaccess.com/1409/?ticket=SYNTHETIC", "cas_redirect_unverified"),
    ("GET", "https://mediatheques.agglo-larochelle.fr/cas/login", 302,
     "https://evil.example/1409/?ticket=SYNTHETIC", "cas_redirect_refused"),
    ("GET", "https://mediatheques.agglo-larochelle.fr/cas/login", 302,
     "https://www.biblioaccess.com/1717/?ticket=SYNTHETIC", "cas_redirect_refused"),
    ("GET", "https://www.biblioaccess.com/1409/Account/Login", 302,
     "https://mediatheques.agglo-larochelle.fr/login", "sso_redirect_unverified"),
    ("GET", "https://www.biblioaccess.com/1409/Account/Login", 302,
     "https://evil.example/login", "sso_redirect_refused"),
    ("POST", "https://mediatheques.agglo-larochelle.fr/logon", 403, "", "authentication_refused"),
    ("POST", "https://mediatheques.agglo-larochelle.fr/logon", 429, "", "rate_limited"),
    ("POST", "https://mediatheques.agglo-larochelle.fr/logon", 500, "", "server_unavailable"),
])
def test_http_observation_codes(method, url, status, location, expected):
    assert mod.classify_http_step(method, url, status, location=location) == expected


def test_form_names_not_values_and_no_auth_inference():
    html = """<form method="post" action="/logon">
     <input name="username" value="PRIVATE-SYNTHETIC"/>
     <input type="password" name="password" value="PASS-SYNTHETIC"/>
     <input type="hidden" name="execution" value="TICKET-SYNTHETIC"/>
    </form>"""
    rows = mod.inspect_login_forms(
        "https://mediatheques.agglo-larochelle.fr/login", html)
    assert len(rows) == 1
    row = rows[0]
    assert row.method == "POST"
    assert row.action_path == "/logon"
    assert row.password_field_present
    assert row.field_names == ("username", "password", "execution")
    assert not row.submit_ready
    for secret in ("PRIVATE-SYNTHETIC", "PASS-SYNTHETIC", "TICKET-SYNTHETIC"):
        assert secret not in repr(rows)


@pytest.mark.parametrize("action", [
    "https://attacker.example/logon", "http://mediatheques.agglo-larochelle.fr/logon",
    "/cas/login", "/logon?token=SYNTHETIC", "https://mediatheques.agglo-larochelle.fr:444/logon",
])
def test_unknown_form_target_refused(action):
    html = f'<form method="post" action="{action}"><input name="username"></form>'
    with pytest.raises(mod.SsoProtocolError, match="form_target_refused"):
        mod.inspect_login_forms("https://mediatheques.agglo-larochelle.fr/login", html)


def test_duplicate_input_and_unclosed_form_rejected():
    page = "https://mediatheques.agglo-larochelle.fr/login"
    with pytest.raises(mod.SsoProtocolError, match="invalid_form"):
        mod.inspect_login_forms(page, '<form action="/logon"><input name="x"><input name="x"></form>')
    with pytest.raises(mod.SsoProtocolError, match="invalid_form"):
        mod.inspect_login_forms(page, '<form action="/logon"><input name="x">')


def test_response_shape_has_no_json_keys_or_values():
    body = b'{"authToken":"PRIVATE-SYNTHETIC","status":"ok"}'
    data = mod.logon_response_shape(
        status=200, content_type="application/json; charset=UTF-8", body=body)
    assert data == {
        "step": "institutional_logon", "status": 200,
        "media_type": "application/json", "body_bytes": len(body),
        "authentication_verified": False, "cas_completed": False}
    assert "PRIVATE-SYNTHETIC" not in repr(data)
    with pytest.raises(mod.SsoProtocolError):
        mod.logon_response_shape(status=200, content_type="text/xml", body=body)
