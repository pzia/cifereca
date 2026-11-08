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


def test_post_mode_invalid_dynamic_field_never_gets_network():
    options = _post_options()
    options["ajax_dc"] = "SYNTHETIC" + chr(10) + "INVALID"
    session = _post_session(options)
    try:
        with pytest.raises(mod.ProviderConfigurationError, match="shape_mismatch"):
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


def test_optional_catalogue_probe_logs_partial_read_without_rights(monkeypatch):
    from types import SimpleNamespace
    import sys
    observed = []
    class Catalogue:
        def __init__(self):
            observed.append("new")
        def search(self, query, *, max_pages):
            assert query == "un roman"
            assert max_pages == 1
            observed.append("search")
            return SimpleNamespace(status="page_budget_exhausted",
                                   book_ids=("123",), pages_observed=1)
        def read_observed_notice(self, book_id):
            assert book_id == "123"
            observed.append("detail")
            return SimpleNamespace(title="SYNTHETIC PRIVATE UNUSED")
        def close(self):
            observed.append("close")
    monkeypatch.setitem(sys.modules, "biblioaccess_catalogue_http",
                        SimpleNamespace(BiblioAccessCatalogueHTTP=Catalogue,
                                        CatalogueHTTPError=ValueError))
    events = []
    class Diagnostics:
        def add(self, cat, op, status, *, detail):
            events.append((status, detail))
    s = mod.LaRochelleSession(
        credential_id="test", label="test", credentials={},
        options={"experimental_catalogue_query": "un roman"},
        diagnostics=Diagnostics(),
    )
    try:
        s._probe_public_catalogue()
        assert observed == ["new", "search", "detail", "close"]
        assert [entry[1]["phase"] for entry in events] == [
            "catalogue_search", "catalogue_search",
            "catalogue_detail", "catalogue_detail"]
        assert events[1][1]["book_ids_observed"] == 1
        assert events[1][1]["completeness"] == "partial"
        assert events[3][1]["entitlements_verified"] is False
        assert "SYNTHETIC PRIVATE UNUSED" not in repr(events)
        assert not s.connected and s.accounts == []
    finally:
        s.close()


def test_no_catalogue_query_means_no_http_or_catalogue_client(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "biblioaccess_catalogue_http",
                        None)
    s = mod.LaRochelleSession(
        credential_id="test", label="test", credentials={},
        options={"experimental_catalogue_query": ""},
    )
    try:
        s._probe_public_catalogue()  # must return before any import
        assert not s.connected
    finally:
        s.close()


@pytest.mark.parametrize("script_path", [
    "https://mediatheques.agglo-larochelle.fr/assets/logon.js",
    "//evil.example/logon.js",
    "/assets/logon.js?token=SECRET",
    "/assets/logon.js#frag",
    "/assets/secret js",
    "/assets/\u00e9tape.js",
    "/" + "a" * 300,
])
def test_selected_script_path_invalid_before_any_network(script_path):
    session = mod.PLUGIN.create_session(
        credential_id="cred", label="test",
        credentials={"username": "SYNTHETIC", "password": "PRIVATE"},
        options={"experimental_http_probe": "on",
                 "experimental_login_script_path": script_path},
        config=None, storage=None, diagnostics=None)
    session._client_factory = lambda: pytest.fail("must fail before network")
    try:
        with pytest.raises(mod.ProviderConfigurationError, match="script_path_invalid"):
            session.connect()
        assert not session.diagnostic_state()["probe_attempted"]
    finally:
        session.close()


def test_explicit_script_probe_uses_observed_path_and_logs_only_structure():
    from types import SimpleNamespace
    recorded = []
    events = []

    class Client:
        def observe_entry_once(self):
            recorded.append("entry")
            return SimpleNamespace(phase="entry_redirect_unverified", status=302)
        def observe_login_once(self):
            recorded.append("login")
            return SimpleNamespace(phase="login_html_unverified", status=200)
        def login_preflight(self):
            recorded.append("preflight")
            return SimpleNamespace(
                script_count=1,
                same_origin_script_paths=("/assets/logon.js",),
                external_script_count=1,
                field_names=("username",), inline_identifier_names=())
        def inspect_login_script_once(self, *, path, consent):
            recorded.append("script")
            assert consent is True and path == "/assets/logon.js"
            return SimpleNamespace(
                identifier_names=("_dc", "captchaCheckValue"),
                ajax_endpoint_referenced=True, executable=False,
                value_provenance_verified=False)
        def close(self):
            recorded.append("close")

    class Diagnostics:
        def add(self, category, operation, status, *, detail):
            events.append((status, detail))

    session = mod.PLUGIN.create_session(
        credential_id="cred", label="test",
        credentials={"username": "PRIVATE-USER", "password": "PRIVATE-PASS"},
        options={"experimental_http_probe": "on",
                 "experimental_login_script_path": "/assets/logon.js"},
        config=None, storage=None, diagnostics=Diagnostics())
    session._client_factory = Client
    try:
        with pytest.raises(mod.ProviderCapabilityError, match="completed_login_not_attempted"):
            session.connect()
        assert recorded == ["entry", "login", "preflight", "script", "close"]
        phases = [detail["phase"] for _, detail in events]
        assert phases[-2:] == ["login_script", "login_script"]
        last = events[-1][1]
        assert last["identifier_names"] == ["_dc", "captchaCheckValue"]
        assert last["ajax_endpoint_referenced"] is True
        assert last["value_provenance_verified"] is False
        assert last["challenge_resolved"] is False
        assert "PRIVATE-PASS" not in repr(events)
        assert not session.connected and session.accounts == []
    finally:
        session.close()


def test_explicit_script_probe_failure_never_sends_post():
    from types import SimpleNamespace
    calls = []
    details = []
    class Client:
        def observe_entry_once(self):
            calls.append("entry")
            return SimpleNamespace(phase="entry_redirect_unverified", status=302)
        def observe_login_once(self):
            calls.append("login")
            return SimpleNamespace(phase="login_html_unverified", status=200)
        def login_preflight(self):
            calls.append("preflight")
            return SimpleNamespace(
                script_count=1, same_origin_script_paths=("/assets/other.js",),
                external_script_count=1, field_names=(),
                inline_identifier_names=())
        def inspect_login_script_once(self, *, path, consent):
            from biblioaccess_ajax_cas import AjaxCasError
            calls.append("script")
            raise AjaxCasError("cas_script_unobserved")
        def submit_once(self, **kwargs):
            pytest.fail("POST must not run if explicit JS probe fails")
        def close(self):
            calls.append("close")
    class Diagnostics:
        def add(self, category, operation, status, *, detail):
            details.append(detail)

    options = _post_options()
    options["experimental_login_script_path"] = "/assets/logon.js"
    session = _post_session(options, client=Client(), diagnostics=Diagnostics())
    try:
        with pytest.raises(mod.ProviderConfigurationError, match="cas_script_unobserved"):
            session.connect()
        assert calls == ["entry", "login", "preflight", "script", "close"]
        assert details[-1]["error_code"] == "cas_script_unobserved"
        assert "logon.js" not in repr(details[-1])
        assert not session.connected and session.accounts == []
    finally:
        session.close()


def test_unique_pagespeed_logon_is_selected_without_manual_account_option():
    from types import SimpleNamespace
    found = "/ui/250297600004/plug-in/logon/logon.js.pagespeed.ce.E1q2XCAAyC.js"
    calls = []
    diagnostics = []
    class Client:
        def observe_entry_once(self):
            calls.append("entry")
            return SimpleNamespace(phase="entry_redirect_unverified", status=302)
        def observe_login_once(self):
            calls.append("login")
            return SimpleNamespace(phase="login_html_unverified", status=200)
        def login_preflight(self):
            calls.append("preflight")
            return SimpleNamespace(
                script_count=3,
                same_origin_script_paths=("/scripts/vendor.js", found, "/assets/app.js"),
                external_script_count=3,
                field_names=("username", "password"),
                inline_identifier_names=())
        def inspect_login_script_once(self, *, path, consent):
            assert consent is True and path == found
            calls.append("script")
            return SimpleNamespace(
                identifier_names=("_dc", "captchaCheckValue"),
                ajax_endpoint_referenced=True)
        def close(self):
            calls.append("close")
    class Diagnostics:
        def add(self, category, operation, status, *, detail):
            diagnostics.append(detail)
    s = mod.PLUGIN.create_session(
        credential_id="synthetic", label="reader",
        credentials={"username": "PRIVATE-USER", "password": "PRIVATE-PASS"},
        options={"experimental_http_probe": "on"},
        config=None, storage=None, diagnostics=Diagnostics())
    s._client_factory = Client
    try:
        with pytest.raises(mod.ProviderCapabilityError, match="completed_login_not_attempted"):
            s.connect()
        assert calls == ["entry", "login", "preflight", "script", "close"]
        assert diagnostics[-1]["phase"] == "login_script"
        assert diagnostics[-1]["selection"] == "auto"
        assert diagnostics[-1]["ajax_endpoint_referenced"] is True
        assert s.diagnostic_state()["probe_phase"] == "script_structure_unverified"
        assert found not in repr(diagnostics[-1])
        assert not s.connected and s.accounts == []
    finally:
        s.close()


@pytest.mark.parametrize("paths", [
    (),
    ("/assets/app.js",),
    ("/scripts/logon.js", "/plugins/logon.js"),
    ("/scripts/logon.js", "/plugins/logon.js.pagespeed.ce.AA.js"),
])
def test_auto_logon_skips_absent_or_ambiguous_sources(paths):
    assert mod._observed_logon_script(paths) == ""


def test_auto_logon_picks_plain_static_script_too():
    assert mod._observed_logon_script(
        ("/assets/jquery.js", "/plug-in/logon/logon.js")
    ) == "/plug-in/logon/logon.js"


def test_auto_post_mode_reuses_one_session_without_manual_ajax_options():
    from types import SimpleNamespace
    events = []
    calls = []
    class Client:
        def observe_entry_once(self):
            calls.append("entry")
            return SimpleNamespace(phase="entry_redirect_unverified", status=302)
        def observe_login_once(self):
            calls.append("login")
            return SimpleNamespace(phase="login_html_unverified", status=200)
        def login_preflight(self):
            calls.append("preflight")
            return SimpleNamespace(
                script_count=0, same_origin_script_paths=(),
                external_script_count=0, field_names=("username",),
                inline_identifier_names=())
        def submit_observed_login_once(self, *, username, password, consent):
            calls.append("auto_post")
            assert consent is True
            assert username == "PRIVATE-SYNTHETIC-USER"
            assert password == "PRIVATE-SYNTHETIC-PASSWORD"
            return SimpleNamespace(status=200, outcome="failure_reported")
        def observe_cas_once(self):
            pytest.fail("no CAS for unsuccessful AJAX response")
        def close(self):
            calls.append("close")
    class Diagnostics:
        def add(self, category, operation, status, *, detail):
            events.append(detail)
    s = mod.PLUGIN.create_session(
        credential_id="cred", label="reader",
        credentials={"username": "PRIVATE-SYNTHETIC-USER",
                     "password": "PRIVATE-SYNTHETIC-PASSWORD"},
        options={"experimental_http_probe": "auto-post"},
        config=None, storage=None, diagnostics=Diagnostics())
    s._client_factory = Client
    try:
        with pytest.raises(mod.ProviderCapabilityError, match="completed_identity_unverified"):
            s.connect()
        assert calls == ["entry", "login", "preflight", "auto_post", "close"]
        assert any(e["phase"] == "ajax_logon" and e.get("outcome") == "failure_reported"
                   for e in events)
        assert "PRIVATE-SYNTHETIC" not in repr(events)
        assert not s.connected and s.accounts == []
    finally:
        s.close()


@pytest.mark.parametrize(("failure_step", "expected_phase"), [
    ("entry", "entry"),
    ("login", "institution_login"),
    ("preflight", "login_preflight"),
    ("post", "ajax_logon"),
    ("cas", "cas_return"),
    ("loans", "loans_page"),
])
def test_error_diagnostic_reports_failing_ssostep_not_generic_preflight(
    failure_step, expected_phase,
):
    from types import SimpleNamespace
    events, calls = [], []

    class Client:
        def step(self, name):
            calls.append(name)
            if name == failure_step:
                raise ValueError("SYNTHETIC-PRIVATE-DETAIL")
        def observe_entry_once(self):
            self.step("entry")
            return SimpleNamespace(phase="entry_redirect_unverified", status=302)
        def observe_login_once(self):
            self.step("login")
            return SimpleNamespace(phase="login_html_unverified", status=200)
        def login_preflight(self):
            self.step("preflight")
            return SimpleNamespace(
                script_count=0, same_origin_script_paths=(),
                external_script_count=0, field_names=(), inline_identifier_names=())
        def submit_observed_login_once(self, **kwargs):
            self.step("post")
            return SimpleNamespace(
                outcome="success_reported", status=200,
                error_count=0, message_present=False)
        def observe_cas_once(self):
            self.step("cas")
            return SimpleNamespace(phase="landing_unverified", status=200)
        def observe_loans_once(self):
            self.step("loans")
            return SimpleNamespace(phase="loans_html_unverified", status=200)
        def close(self):
            calls.append("closed")

    class Diagnostics:
        def add(self, category, operation, status, *, detail):
            events.append((status, detail))

    session = mod.PLUGIN.create_session(
        credential_id="synthetic", label="reader",
        credentials={"username": "SYNTHETIC", "password": "SYNTHETIC"},
        options={"experimental_http_probe": "auto-post"},
        config=None, storage=None, diagnostics=Diagnostics())
    session._client_factory = Client
    try:
        with pytest.raises(mod.ProviderConfigurationError, match="biblioaccess_probe_http_failure"):
            session.connect()
        status, detail = events[-1]
        assert status == "error"
        assert detail["phase"] == expected_phase
        assert detail["error_code"] == "biblioaccess_probe_http_failure"
        assert "SYNTHETIC-PRIVATE-DETAIL" not in repr(events)
        assert calls[-1] == "closed"
        assert not session.connected and session.accounts == []
    finally:
        session.close()


def test_ajax_failure_diagnostic_reports_only_json_presence_and_error_count():
    from types import SimpleNamespace
    events = []

    class Client:
        def observe_entry_once(self):
            return SimpleNamespace(phase="entry_redirect_unverified", status=302)
        def observe_login_once(self):
            return SimpleNamespace(phase="login_html_unverified", status=200)
        def login_preflight(self):
            return SimpleNamespace(
                script_count=0, same_origin_script_paths=(),
                external_script_count=0, field_names=(), inline_identifier_names=())
        def submit_observed_login_once(self, **kwargs):
            return SimpleNamespace(
                status=200, outcome="failure_reported",
                error_count=2, message_present=True)
        def close(self):
            pass

    class Diagnostics:
        def add(self, category, operation, status, *, detail):
            events.append(detail)

    session = mod.PLUGIN.create_session(
        credential_id="synthetic", label="reader",
        credentials={"username": "SYNTHETIC", "password": "SYNTHETIC"},
        options={"experimental_http_probe": "auto-post"},
        config=None, storage=None, diagnostics=Diagnostics())
    session._client_factory = Client
    try:
        with pytest.raises(mod.ProviderCapabilityError, match="completed_identity_unverified"):
            session.connect()
        answer = next(x for x in events if x["phase"] == "ajax_logon"
                      and x.get("outcome") == "failure_reported")
        assert answer["reported_error_count"] == 2
        assert answer["response_message_present"] is True
        assert answer["identity_verified"] is False
        assert session.accounts == [] and not session.connected
    finally:
        session.close()
