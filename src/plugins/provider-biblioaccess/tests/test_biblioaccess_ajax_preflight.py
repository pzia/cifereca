"""Offline AJAX dynamic preflight: no browser, no network, no token extraction."""
import importlib.util
from pathlib import Path
import sys

import pytest

path = Path(__file__).resolve().parents[1] / "biblioaccess_ajax_preflight.py"
spec = importlib.util.spec_from_file_location("biblioaccess_ajax_preflight", path)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_institutional_login_script_and_field_names_only():
    html = ('<html><head><script src="/assets/login.min.js?v=PRIVATE-SYNTHETIC"></script>'
            '<script src="https://evil.example/captcha.js"></script>'
            '<script>const _dc = "SECRET"; const captchaCheckValue = "SECRET2";</script>'
            '</head><body><form>'
            '<input name="username" value="PRIVATE-USERNAME">'
            '<input type="password" name="password" value="PRIVATE-PASSWORD">'
            '<input name="captchaCheckValue" value="PRIVATE-CHALLENGE">'
            '</form></body></html>')
    out = mod.inspect_login_preflight(html)
    assert out.script_count == 3
    assert out.external_script_count == 2
    assert out.inline_script_count == 1
    assert out.same_origin_script_paths == ("/assets/login.min.js",)
    assert out.field_names == ("username", "password", "captchaCheckValue")
    assert out.inline_identifier_names == ("_dc", "captchaCheckValue")
    assert out.status == "unverified"
    assert not out.values_extracted and not out.challenge_resolved
    for secret in ("PRIVATE-USERNAME", "PRIVATE-PASSWORD", "PRIVATE-CHALLENGE",
                   "PRIVATE-SYNTHETIC", "SECRET2"):
        assert secret not in repr(out)


def test_absence_of_dynamic_identifiers_does_not_prove_absence_of_challenge():
    out = mod.inspect_login_preflight('<html><script src="/app.js"></script></html>')
    assert out.inline_identifier_names == ()
    assert not out.challenge_resolved
    assert not out.values_extracted


@pytest.mark.parametrize("src", [
    "http://mediatheques.agglo-larochelle.fr/sso.js",
    "https://evil.example/sso.js",
    "//evil.example/sso.js",
    "javascript:alert(1)",
    "/sso.json",
])
def test_foreign_or_non_script_sources_ignored(src):
    out = mod.inspect_login_preflight('<script src="' + src + '"></script>')
    assert out.script_count == 1
    assert not out.same_origin_script_paths


def test_limits_and_invalid_urls_rejected():
    with pytest.raises(mod.LoginPreflightError, match="limit"):
        mod.inspect_login_preflight("x" * 270000)
    with pytest.raises(mod.LoginPreflightError, match="url_refused"):
        mod.inspect_login_preflight("<html></html>",
            page_url="https://attacker.example/login")
    with pytest.raises(mod.LoginPreflightError, match="limit"):
        mod.inspect_login_preflight("<script></script>" * 33)


def test_duplicate_input_names_observed_only_once():
    out = mod.inspect_login_preflight(
        '<form><input name="username" value="one">'
        '<input name="username" value="two">'
        '<input name="password"></form>')
    assert out.field_names == ("username", "password")


def test_js_evidence_recognizes_only_names_and_route_not_computation():
    observation = mod.inspect_javascript_evidence(
        'function f(){ const _dc = "PRIVATE-DYNAMIC";'
        ' const captchaCheckValue = "PRIVATE-CHALLENGE";'
        ' return "/logon.svc/logon"; }')
    assert observation.identifier_names == ("_dc", "captchaCheckValue")
    assert observation.ajax_endpoint_referenced
    assert not observation.executable
    assert not observation.value_provenance_verified
    assert "PRIVATE" not in repr(observation)
    with pytest.raises(mod.LoginPreflightError, match="limit"):
        mod.inspect_javascript_evidence("x" * 200000)
