"""Synthetic regression tests based on observed HAR *shape*, not private bytes."""
import importlib.util
from pathlib import Path
import sys

import httpx
import pytest

path = Path(__file__).resolve().parents[1] / "biblioaccess_ajax_logon.py"
spec = importlib.util.spec_from_file_location("biblioaccess_ajax_logon", path)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

QUERY = {"changeUser": "false", "provider": "0", "_dc": "1234567890123"}
FORM = {
    "username": "SYNTHETIC-USER",
    "password": "SYNTHETIC-PASSWORD",
    "rememberMe": "false",
    "captcha": "",
    "captchaCheckValue": "SYNTHETIC-CHALLENGE",
}


def test_ajax_endpoint_fields_and_status_never_connect_core():
    seen = []
    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.host == "mediatheques.agglo-larochelle.fr"
        assert request.url.path == "/logon.svc/logon"
        assert set(request.url.params) == set(QUERY)
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert request.headers["x-requested-with"] == "XMLHttpRequest"
        assert set(k for k, _ in __import__("urllib.parse", fromlist=["parse_qsl"]).parse_qsl(
            request.content.decode("ascii"), keep_blank_values=True)) == set(FORM)
        return httpx.Response(200, headers={"content-type": "application/json; charset=utf-8"},
                              json={"errors": [], "message": None,
                                    "success": True, "d": "SYNTHETIC-OPAQUE"})
    client = mod.ObservedAjaxLogonClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(mod.AjaxLogonError, match="consent_required"):
            client.submit_once(query=QUERY, fields=FORM, consent=False)
        assert not seen
        result = client.submit_once(query=QUERY, fields=FORM, consent=True)
        assert len(seen) == 1
        assert result.outcome == "success_reported"
        assert not result.cas_completed and not result.identity_verified
        assert not result.core_connected
        assert "SYNTHETIC-OPAQUE" not in repr(result)
        assert "SYNTHETIC-PASSWORD" not in repr(result)
        with pytest.raises(mod.AjaxLogonError, match="already_attempted"):
            client.submit_once(query=QUERY, fields=FORM, consent=True)
    finally:
        client.close()


@pytest.mark.parametrize("changed_query,changed_form", [
    ({"changeUser": "false", "provider": "0"}, FORM),
    (QUERY, {"username": "x", "password": "y"}),
    ({"changeUser": "false", "provider": "0", "_dc": "123", "token": "secret"}, FORM),
])
def test_shape_changes_refused_without_post(changed_query, changed_form):
    client = mod.ObservedAjaxLogonClient(transport=httpx.MockTransport(
        lambda _: pytest.fail("No POST allowed")))
    try:
        with pytest.raises(mod.AjaxLogonError):
            client.submit_once(query=changed_query, fields=changed_form, consent=True)
    finally:
        client.close()


def test_302_never_followed_or_interpreted_as_success():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(302, headers={"location": "https://evil.example/x",
                                             "content-type": "application/json"},
                              json={"success": True})
    client = mod.ObservedAjaxLogonClient(transport=httpx.MockTransport(handler))
    try:
        result = client.submit_once(query=QUERY, fields=FORM, consent=True)
        assert result.outcome == "failure_reported"
        assert calls == ["/logon.svc/logon"]
    finally:
        client.close()


def test_failure_is_not_retried():
    calls = []
    def handler(request):
        calls.append(request.method)
        raise httpx.ConnectError("SYNTHETIC-SECRET", request=request)
    client = mod.ObservedAjaxLogonClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(mod.AjaxLogonError, match="outcome_unknown"):
            client.submit_once(query=QUERY, fields=FORM, consent=True)
        with pytest.raises(mod.AjaxLogonError, match="already_attempted"):
            client.submit_once(query=QUERY, fields=FORM, consent=True)
        assert calls == ["POST"]
    finally:
        client.close()


def test_json_payload_opaque_and_bounded():
    with pytest.raises(mod.AjaxLogonError, match="json_shape"):
        mod.inspect_ajax_response(status=200, mime="application/json",
                                  data=b'{"success":true,"sessionToken":"SECRET"}')
    result = mod.inspect_ajax_response(
        status=200, mime="application/json",
        data=b'{"errors":[],"message":null,"success":false,"d":"SECRET"}')
    assert result.outcome == "failure_reported"
    assert "SECRET" not in repr(result)
    client = mod.ObservedAjaxLogonClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "application/json"},
                                 content=b"x" * 65537)))
    try:
        with pytest.raises(mod.AjaxLogonError, match="response_limit"):
            client.submit_once(query=QUERY, fields=FORM, consent=True)
    finally:
        client.close()


def test_explicit_empty_captcha_check_value_is_not_assumed_invalid():
    calls = []
    def handler(request):
        calls.append(request.content.decode("ascii"))
        return httpx.Response(200, headers={"content-type": "application/json"},
                              json={"success": False, "errors": [], "message": None})
    client = mod.ObservedAjaxLogonClient(transport=httpx.MockTransport(handler))
    try:
        result = client.submit_once(
            query=QUERY, fields={**FORM, "captchaCheckValue": ""}, consent=True)
        assert result.outcome == "failure_reported"
        assert len(calls) == 1
        assert "captchaCheckValue=" in calls[0]
    finally:
        client.close()


@pytest.mark.parametrize("payload", [
    b'{"success":true,"errors":["SYNTHETIC-FAILURE"]}',
    b'{"success":true,"errors":{"code":"SYNTHETIC-FAILURE"}}',
    b'{"success":true,"errors":null}',
    b'{"success":true,"errors":"SYNTHETIC-FAILURE"}',
])
def test_ajax_contradictory_errors_cannot_report_success(payload):
    observation = mod.inspect_ajax_response(
        status=200, mime="application/json", data=payload)
    assert observation.outcome == "unknown"
    assert not observation.identity_verified
    assert not observation.cas_completed
    assert "SYNTHETIC-FAILURE" not in repr(observation)


@pytest.mark.parametrize("payload", [
    b'{"success":false,"success":true,"errors":[]}',
    b'{"success":true,"errors":[],"errors":["SYNTHETIC-FAILURE"]}',
    b'{"success":true,"errors":[],"d":{"a":1,"a":2}}',
    b'{"success":true,"errors":[],"d":NaN}',
    b'{"success":true,"errors":[],"d":Infinity}',
])
def test_ajax_ambiguous_json_is_refused(payload):
    with pytest.raises(mod.AjaxLogonError, match="ajax_logon_json_invalid"):
        mod.inspect_ajax_response(
            status=200, mime="application/json", data=payload)


def test_ajax_reported_success_without_errors_key_remains_unverified():
    observation = mod.inspect_ajax_response(
        status=200, mime="application/json",
        data=b'{"success":true,"d":"SYNTHETIC-OPAQUE"}')
    assert observation.outcome == "success_reported"
    assert not observation.cas_completed
    assert not observation.identity_verified
    assert "SYNTHETIC-OPAQUE" not in repr(observation)
