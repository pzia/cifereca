"""HTTP-only login qualification with a synthetic transport: no network or browser."""
import importlib.util
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sso = load("biblioaccess_http_sso")
probe = load("biblioaccess_http_probe")


def mock(handler):
    return probe.BiblioAccessHTTPQualification(transport=httpx.MockTransport(handler))


def test_read_login_then_single_post_never_attests_authentication():
    requests = []
    def handler(request):
        requests.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, headers={"Content-Type": "text/html"},
                text='<form method="POST" action="/logon">'
                     '<input name="login" value="SYNTHETIC"/>'
                     '<input type="password" name="password"/>'
                     '<input type="hidden" name="csrf" value="SYNTHETIC"/>'
                     '</form>')
        assert request.method == "POST"
        assert request.url.path == "/logon"
        return httpx.Response(200, headers={"Content-Type": "application/json"},
                              json={"status": "ok", "access_token": "SYNTHETIC"})
    client = mock(handler)
    try:
        shapes = client.inspect_login()
        assert len(shapes) == 1
        assert shapes[0].field_names == ("login", "password", "csrf")
        assert not shapes[0].submit_ready
        with pytest.raises(sso.SsoProtocolError, match="consent_required"):
            client.submit_once(shape=shapes[0],
                fields={"login": "user", "password": "pass", "csrf": "synthetic"},
                explicit_consent=False)
        assert [method for method, _ in requests] == ["GET"]
        observed = client.submit_once(
            shape=shapes[0],
            fields={"login": "SYNTHETIC-USER",
                    "password": "SYNTHETIC-PASS", "csrf": "SYNTHETIC"},
            explicit_consent=True)
        assert [method for method, _ in requests] == ["GET", "POST"]
        assert observed.post_sent
        assert not observed.authentication_verified
        assert not observed.cas_completed
        assert "SYNTHETIC-PASS" not in repr(observed)
        assert "access_token" not in repr(observed)
        with pytest.raises(sso.SsoProtocolError, match="post_already_attempted"):
            client.submit_once(shape=shapes[0], fields={
                "login": "another", "password": "another", "csrf": "another"},
                explicit_consent=True)
        assert [method for method, _ in requests] == ["GET", "POST"]
    finally:
        client.close()


def test_bad_form_shape_no_network_post():
    client = mock(lambda request: pytest.fail("Unexpected HTTP request"))
    try:
        shape = sso.FormShape("POST", sso.ORIGIN, "/logon",
                              ("login", "password"), True)
        with pytest.raises(sso.SsoProtocolError, match="schema_unqualified"):
            client.submit_once(shape=shape,
                fields={"login": "SYNTHETIC"}, explicit_consent=True)
    finally:
        client.close()


def test_post_30x_is_not_followed():
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(302, headers={
            "location": "https://evil.example/accept", "content-type": "text/html"
        }, text="")
    client = mock(handler)
    shape = sso.FormShape("POST", sso.ORIGIN, "/logon",
                          ("login", "password"), True)
    client._inspected_form_shapes = (shape,)
    client._login_inspected = True
    try:
        answer = client.submit_once(shape=shape,
                fields={"login": "x", "password": "y"},
                explicit_consent=True)
        assert answer.status == 302
        assert not answer.authentication_verified
        assert seen == ["/logon"]
    finally:
        client.close()


def test_post_failure_not_retried():
    sent = []
    def handler(request):
        sent.append(request.method)
        raise httpx.ConnectError("PRIVATE-SYNTHETIC", request=request)
    client = mock(handler)
    shape = sso.FormShape("POST", sso.ORIGIN, "/logon",
                          ("login", "password"), True)
    client._inspected_form_shapes = (shape,)
    client._login_inspected = True
    try:
        with pytest.raises(sso.SsoProtocolError, match="post_outcome_unknown"):
            client.submit_once(shape=shape,
                fields={"login": "x", "password": "y"}, explicit_consent=True)
        assert sent == ["POST"]
    finally:
        client.close()


def test_large_body_refused_without_trusting_content_length():
    client = mock(lambda request: httpx.Response(
        200, headers={"Content-Type": "application/json"}, content=b"x" * 66000))
    shape = sso.FormShape("POST", sso.ORIGIN, "/logon",
                          ("login", "password"), True)
    client._inspected_form_shapes = (shape,)
    client._login_inspected = True
    try:
        with pytest.raises(sso.SsoProtocolError, match="response_limit"):
            client.submit_once(shape=shape,
                fields={"login": "x", "password": "y"}, explicit_consent=True)
    finally:
        client.close()


def test_reject_forged_form_even_when_fields_match():
    client = mock(lambda request: pytest.fail("Unexpected HTTP request"))
    shape = sso.FormShape("POST", sso.ORIGIN, "/logon", ("login", "password"), True)
    try:
        with pytest.raises(sso.SsoProtocolError, match="schema_unqualified"):
            client.submit_once(shape=shape,
                               fields={"login": "x", "password": "y"},
                               explicit_consent=True)
    finally:
        client.close()
