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
    with pytest.raises(mod.ProviderConfigurationError, match="cas_entry_redirect_refused"):
        session.connect()
    assert client.calls == ["entry", "close"]
    assert not session.connected and session.accounts == []
    assert diag.events[-1][1]["error_code"] == "cas_entry_redirect_refused"
    session.close()

def test_no_query_or_ticket_retained():
    classification = mod.classify_sso_transition(
        "https://www.biblioaccess.com/1409/?ticket=SYNTHETIC-PRIVATE", 200
    )
    assert classification == "landing_unverified"
    assert "SYNTHETIC-PRIVATE" not in classification
