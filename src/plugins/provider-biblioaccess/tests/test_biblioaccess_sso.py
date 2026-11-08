"""Entirely synthetic SSO transitions; never fixtures from a private trace."""
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "cifereca_biblioaccess.py"
spec = importlib.util.spec_from_file_location("cifereca_biblioaccess", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.mark.parametrize(("url", "status", "expected"), [
    ("https://www.biblioaccess.com/1409/Account/Login", 302, "redirect_to_idp"),
    ("https://mediatheques.agglo-larochelle.fr/login?service=fake", 200, "interactive_login_page"),
    ("https://mediatheques.agglo-larochelle.fr/cas/login?service=fake", 302, "cas_redirect_unverified"),
    ("https://www.biblioaccess.com/1409/?ticket=SYNTHETIC", 200, "landing_unverified"),
    ("https://www.biblioaccess.com/1409/Catalog/MyLoans", 200, "loans_page_unverified"),
    ("https://www.biblioaccess.com/1717/Account/Login", 302, "unsupported"),
    ("https://evil.example/1409/Account/Login", 302, "unsupported"),
    ("http://www.biblioaccess.com/1409/Account/Login", 302, "unsupported"),
    ("https://www.biblioaccess.com.evil.example/1409/Account/Login", 302, "unsupported"),
    ("https://user:secret@www.biblioaccess.com/1409/Account/Login", 302, "unsupported"),
    ("https://www.biblioaccess.com:444/1409/Account/Login", 302, "unsupported"),
    ("https://www.biblioaccess.com/1409/Account/Login#fragment", 302, "unsupported"),
    ("https://www.biblioaccess.com/1409/Account/Login", 401, "unsupported"),
    ("https://www.biblioaccess.com/1409/Catalog/MyLoans", 403, "unsupported"),
])
def test_synthetic_routes(url, status, expected):
    assert mod.classify_sso_transition(url, status) == expected


def test_configurable_account_probe_opt_in_no_automatic_network():
    p = mod.PLUGIN
    assert p.descriptor.state == "ready"
    assert p.descriptor.interactive_authentication
    assert p.descriptor.capabilities.names() == []
    assert [f.key for f in p.descriptor.credential_fields] == ["username", "password"]
    assert p.descriptor.credential_fields[1].secret
    session = p.create_session(
        credential_id="fake-credential", label="fake",
        credentials={"username": "SYNTHETIC", "password": "PRIVATE-SYNTHETIC"},
        options={"experimental_http_probe": "off"},
        config=None, storage=None, diagnostics=None,
    )
    assert session.provider_id == "biblioaccess.la-rochelle"
    assert session.credential_id == "fake-credential"
    assert session.credentials["username"] == "SYNTHETIC"
    with pytest.raises(mod.ProviderCapabilityError, match="probe_opt_in_required"):
        session.connect()
    assert session.accounts == []
    assert not session.connected
    session.close()
    assert not session.connected
    assert session.credentials == {}


def test_opt_in_get_probe_reports_stages_but_never_connects():
    class Stage:
        def __init__(self, phase, status):
            self.phase, self.status = phase, status

    class Preflight:
        script_count = 2
        same_origin_script_paths = ("/scripts/login.js",)
        external_script_count = 1
        field_names = ("username", "password")
        inline_identifier_names = ("_dc",)
 
    class FakeClient:
        calls = []
        def observe_entry_once(self):
            self.calls.append("entry")
            return Stage("entry_redirect_unverified", 302)
        def observe_login_once(self):
            self.calls.append("login")
            return Stage("login_html_unverified", 200)
        def login_preflight(self):
            self.calls.append("preflight")
            return Preflight()
        def close(self):
            self.calls.append("close")

    class Diagnostics:
        def __init__(self):
            self.events = []
        def add(self, category, operation, status, *, detail):
            self.events.append((category, operation, status, detail))

    client = FakeClient()
    diagnostics = Diagnostics()
    session = mod.PLUGIN.create_session(
        credential_id="cred-1", label="reader",
        credentials={"username": "PRIVATE-USER", "password": "PRIVATE-PASS"},
        options={"experimental_http_probe": "on"}, config=None, storage=None,
        diagnostics=diagnostics,
    )
    session._client_factory = lambda: client
    with pytest.raises(mod.ProviderCapabilityError, match="completed_login_not_attempted"):
        session.connect()
    assert client.calls == ["entry", "login", "preflight", "close"]
    assert not session.connected and session.accounts == []
    assert session.diagnostic_state()["probe_phase"] == "login_html_unverified"
    assert [x[2] for x in diagnostics.events] == [
        "started", "observed", "started", "observed", "observed"]
    assert diagnostics.events[-1][3]["field_names"] == ["username", "password"]
    assert diagnostics.events[-1][3]["challenge_resolved"] is False
    assert "PRIVATE-PASS" not in repr(diagnostics.events)
    with pytest.raises(mod.ProviderCapabilityError, match="already_attempted"):
        session.connect()
    assert client.calls == ["entry", "login", "preflight", "close"]
    session.close()


def test_probe_failure_logged_but_no_account_promoted():
    class BadClient:
        calls = []
        def observe_entry_once(self):
            self.calls.append("entry")
            raise ValueError("cas_entry_redirect_refused")
        def close(self):
            self.calls.append("close")
    class Diagnostics:
        events = []
        def add(self, category, operation, status, *, detail):
            self.events.append((status, detail))
    client = BadClient()
    diag = Diagnostics()
    session = mod.PLUGIN.create_session(
        credential_id="cred-1", label="reader",
        credentials={"username": "x", "password": "y"},
        options={"experimental_http_probe": "on"}, config=None, storage=None,
        diagnostics=diag,
    )
    session._client_factory = lambda: client
    with pytest.raises(mod.ProviderConfigurationError, match="biblioaccess_probe_http_failure"):
        session.connect()
    assert client.calls == ["entry", "close"]
    assert not session.connected and session.accounts == []
    assert diag.events[-1][1]["error_code"] == "biblioaccess_probe_http_failure"
    session.close()

def test_no_query_or_ticket_retained():
    classification = mod.classify_sso_transition(
        "https://www.biblioaccess.com/1409/?ticket=SYNTHETIC-PRIVATE", 200
    )
    assert classification == "landing_unverified"
    assert "SYNTHETIC-PRIVATE" not in classification


def _post_options():
    return {
        "experimental_http_probe": "post",
        "ajax_change_user": "false",
        "ajax_provider": "SYNTHETIC-IDP",
        "ajax_dc": "SYNTHETIC-FRESH-DYNAMIC",
        "ajax_remember_me": "false",
        "ajax_captcha": "",
        "ajax_captcha_check_value": "SYNTHETIC-FRESH-CHALLENGE",
    }


def _post_session(options, client=None, diagnostics=None):
    session = mod.PLUGIN.create_session(
        credential_id="synthetic-credential", label="fake-reader",
        credentials={"username": "SYNTHETIC-USERNAME",
                     "password": "PRIVATE-SYNTHETIC-PASSWORD"},
        options=options, config=None, storage=None, diagnostics=diagnostics,
    )
    if client is not None:
        session._client_factory = lambda: client
    return session


def test_post_mode_never_gets_network_when_dynamic_input_missing():
    for missing in ("ajax_dc", "ajax_captcha_check_value",
                    "ajax_remember_me", "ajax_provider", "ajax_captcha"):
        options = _post_options()
        options.pop(missing)
        session = _post_session(options, client=lambda: pytest.fail("no HTTP"))
        try:
            with pytest.raises(mod.ProviderConfigurationError,
                               match="post_inputs_required"):
                session.connect()
            assert not session.connected
            assert session.accounts == []
            assert not session.diagnostic_state()["probe_attempted"]
        finally:
            session.close()


def test_post_mode_missing_or_invalid_challenge_never_gets_network():
    options = _post_options()
    options["ajax_captcha_check_value"] = ""
    session = _post_session(options)
    try:
        with pytest.raises(mod.ProviderConfigurationError, match="challenge_missing"):
            session.connect()
        assert not session.diagnostic_state()["probe_attempted"]
    finally:
        session.close()


def test_realistic_post_cas_myloans_diagnostics_still_never_connect():
    class Stage:
        def __init__(self, phase, status):
            self.phase, self.status = phase, status
    class Preflight:
        script_count = 1
        same_origin_script_paths = ("/login.js",)
        external_script_count = 1
        field_names = ("username", "password")
        inline_identifier_names = ("_dc", "captchaCheckValue")
    class AjaxResult:
        status = 200
        outcome = "success_reported"
    class Client:
        def __init__(self):
            self.calls = []
        def observe_entry_once(self):
            self.calls.append("entry")
            return Stage("entry_redirect_unverified", 302)
        def observe_login_once(self):
            self.calls.append("login")
            return Stage("login_html_unverified", 200)
        def login_preflight(self):
            self.calls.append("preflight")
            return Preflight()
        def submit_once(self, *, query, fields, consent):
            self.calls.append("post")
            assert consent is True
            assert query == {
                "changeUser": "false", "provider": "SYNTHETIC-IDP",
                "_dc": "SYNTHETIC-FRESH-DYNAMIC",
            }
            assert fields == {
                "username": "SYNTHETIC-USERNAME",
                "password": "PRIVATE-SYNTHETIC-PASSWORD",
                "rememberMe": "false", "captcha": "",
                "captchaCheckValue": "SYNTHETIC-FRESH-CHALLENGE",
            }
            return AjaxResult()
        def observe_cas_once(self):
            self.calls.append("cas")
            return Stage("landing_unverified", 200)
        def observe_loans_once(self):
            self.calls.append("loans")
            return Stage("loans_html_unverified", 200)
        def close(self):
            self.calls.append("close")
    class Diagnostics:
        def __init__(self):
            self.events = []
        def add(self, category, operation, status, *, detail):
            self.events.append((category, operation, status, detail))

    diagnostics = Diagnostics()
    client = Client()
    session = _post_session(_post_options(), client=client, diagnostics=diagnostics)
    try:
        with pytest.raises(mod.ProviderCapabilityError,
                           match="completed_identity_unverified"):
            session.connect()
        assert client.calls == [
            "entry", "login", "preflight", "post", "cas", "loans", "close"]
        assert session.accounts == [] and not session.connected
        assert session.diagnostic_state()["probe_phase"] == "loans_html_unverified"
        phases = [x[3]["phase"] for x in diagnostics.events]
        assert phases == [
            "entry", "entry", "institution_login", "institution_login",
            "login_preflight", "ajax_logon", "ajax_logon",
            "cas_return", "cas_return", "loans_page", "loans_page"]
        for secret in (
            "PRIVATE-SYNTHETIC-PASSWORD", "SYNTHETIC-FRESH-CHALLENGE",
            "SYNTHETIC-FRESH-DYNAMIC", "SYNTHETIC-USERNAME",
        ):
            assert secret not in repr(diagnostics.events)
        with pytest.raises(mod.ProviderCapabilityError, match="already_attempted"):
            session.connect()
        assert len(client.calls) == 7
    finally:
        session.close()


def test_reported_failed_ajax_does_not_fetch_cas_or_loans():
    class Stage:
        def __init__(self, phase, status):
            self.phase, self.status = phase, status
    class Preflight:
        script_count = 0
        same_origin_script_paths = ()
        external_script_count = 0
        field_names = ()
        inline_identifier_names = ()
    class Result:
        status = 200
        outcome = "failure_reported"
    class Client:
        def __init__(self):
            self.calls = []
        def observe_entry_once(self):
            self.calls.append("entry")
            return Stage("entry_redirect_unverified", 302)
        def observe_login_once(self):
            self.calls.append("login")
            return Stage("login_html_unverified", 200)
        def login_preflight(self):
            self.calls.append("preflight")
            return Preflight()
        def submit_once(self, **kwargs):
            self.calls.append("post")
            return Result()
        def observe_cas_once(self):
            pytest.fail("CAS must not be called after denied POST")
        def observe_loans_once(self):
            pytest.fail("MyLoans must not be called after denied POST")
        def close(self):
            self.calls.append("close")
    client = Client()
    session = _post_session(_post_options(), client=client)
    try:
        with pytest.raises(mod.ProviderCapabilityError,
                           match="completed_identity_unverified"):
            session.connect()
        assert client.calls == ["entry", "login", "preflight", "post", "close"]
        assert not session.connected
    finally:
        session.close()


def test_post_exception_is_not_echoed_in_journal():
    class Stage:
        def __init__(self, phase, status):
            self.phase, self.status = phase, status
    class Preflight:
        script_count = 0
        same_origin_script_paths = ()
        external_script_count = 0
        field_names = ()
        inline_identifier_names = ()
    class Client:
        def observe_entry_once(self):
            return Stage("entry_redirect_unverified", 302)
        def observe_login_once(self):
            return Stage("login_html_unverified", 200)
        def login_preflight(self):
            return Preflight()
        def submit_once(self, **kwargs):
            raise ValueError("SECRET-RAW-ERROR-DATA")
        def close(self):
            pass
    class Diagnostics:
        def __init__(self):
            self.events = []
        def add(self, category, operation, status, *, detail):
            self.events.append(detail)
    diagnostics = Diagnostics()
    session = _post_session(_post_options(), client=Client(), diagnostics=diagnostics)
    try:
        with pytest.raises(mod.ProviderConfigurationError,
                           match="biblioaccess_probe_http_failure"):
            session.connect()
        assert "SECRET-RAW-ERROR-DATA" not in repr(diagnostics.events)
        assert session.accounts == []
    finally:
        session.close()
