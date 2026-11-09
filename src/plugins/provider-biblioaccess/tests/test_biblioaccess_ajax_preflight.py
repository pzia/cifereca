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


def test_dynamic_field_names_never_escape_login_preflight():
    html = (
        '<input name="username" value="SYNTHETIC-SECRET">'
        '<input name="session_ABCDEF0123456789ABCDEF0123456789">'
        '<input name="opaqueCredentialToken">'
        '<input name="RequestKey">'
        '<input name="captchaCheckValue">'
    )
    observation = mod.inspect_login_preflight(html)
    assert observation.field_names == (
        "username", "RequestKey", "captchaCheckValue")
    assert "ABCDEF0123456789" not in repr(observation)
    assert "opaqueCredentialToken" not in repr(observation)


@pytest.mark.parametrize("script_src", [
    "/assets/session_0123456789abcdef0123456789abcdef/logon.js",
    "/assets/abc0123456789abcdef0123456789abcdef.js",
    "/assets/%2Fsecret/logon.js",
    "/assets/%2e%2e/logon.js",
    "/assets/verylongopaque_token_0123456789abcdef0123456789abcdef.js",
    "/assets/../logon.js",
])
def test_opaque_or_encoded_script_paths_never_reach_diagnostics(script_src):
    observation = mod.inspect_login_preflight(
        '<script src="' + script_src + '"></script>')
    assert observation.script_count == 1
    assert observation.same_origin_script_paths == ()


def test_static_login_script_path_remains_eligible():
    observation = mod.inspect_login_preflight(
        '<script src="/scripts/logon.js?v=PRIVATE-CACHE"></script>'
        '<input name="checkValue"><input name="rememberMe">'
    )
    assert observation.same_origin_script_paths == ("/scripts/logon.js",)
    assert observation.field_names == ("checkValue", "rememberMe")
    assert "PRIVATE-CACHE" not in repr(observation)


def test_script_query_is_private_and_bound_to_observed_source():
    secret = "SYNTHETIC-OPAQUE-QUERY"
    result = mod.inspect_login_preflight(
        '<script src="/scripts/logon.js?v=' + secret + '"></script>')
    assert result.same_origin_script_paths == ("/scripts/logon.js",)
    assert result._script_request_urls == (
        ("/scripts/logon.js",
         mod.IDP + "/scripts/logon.js?v=" + secret),)
    assert secret not in repr(result)


def test_same_path_with_distinct_query_versions_is_ambiguous_and_omitted():
    result = mod.inspect_login_preflight(
        '<script src="/scripts/logon.js?v=SYNTHETIC-ONE"></script>'
        '<script src="/scripts/logon.js?v=SYNTHETIC-TWO"></script>')
    assert result.script_count == 2
    assert result.same_origin_script_paths == ()
    assert result._script_request_urls == ()
    assert "SYNTHETIC" not in repr(result)



def test_realistic_concatenated_ajax_endpoint_without_leading_slash():
    """A PageSpeed JS literal may be relative to instanceWebDirectory."""
    observed = mod.inspect_javascript_evidence(
        "var url = master.instanceWebDirectory + "
        "'logon.svc/logon?_dc=' + new Date().getTime(); "
        "var captchaCheckValue = $('#logon-captcha-checkvalue').val();")
    assert observed.ajax_endpoint_referenced
    assert observed.identifier_names == ("_dc", "captcha", "captchaCheckValue")
    assert not observed.value_provenance_verified


@pytest.mark.parametrize("source", [
    "formService.svc/generateCaptchaValue?length=6",
    "'logon.svc/generateCaptchaValue'",
    "'logon.svc/other'",
])
def test_unrelated_institutional_ajax_endpoint_not_reported_as_logon(source):
    assert not mod.inspect_javascript_evidence(source).ajax_endpoint_referenced
