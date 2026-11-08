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


def test_no_live_capabilities_and_fail_closed():
    p = mod.PLUGIN
    assert p.descriptor.state == "skeleton"
    assert p.descriptor.interactive_authentication
    assert p.descriptor.capabilities.names() == []
    session = p.create_session(
        credential_id="fake-credential", label="fake",
        credentials={"secret": "SYNTHETIC"}, options={"tenant_id": "1717"},
        config=None, storage=None, diagnostics=None,
    )
    assert session.provider_id == "biblioaccess.la-rochelle"
    assert session.credential_id == "fake-credential"
    assert session.credentials == {}
    with pytest.raises(mod.ProviderCapabilityError, match="http_sso_logon_unqualified"):
        session.connect()
    assert session.accounts == []
    assert not session.connected
    session.close()
    assert not session.connected


def test_no_query_or_ticket_retained():
    classification = mod.classify_sso_transition(
        "https://www.biblioaccess.com/1409/?ticket=SYNTHETIC-PRIVATE", 200
    )
    assert classification == "landing_unverified"
    assert "SYNTHETIC-PRIVATE" not in classification
