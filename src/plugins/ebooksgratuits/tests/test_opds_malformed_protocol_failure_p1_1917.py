"""#1917: malformed supplier Atom is not a zero; DTD is a safety refusal.

Offline httpx transport; never invoke live OPDS, downloads or private rights.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca.search_failures import search_failure_code
from cifereca.core.known_catalog_fallback import public_service_unavailable
from cifereca_ebooksgratuits import EbooksSession


ROOT = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        '<id>urn:root</id><title>All books</title>'
        '<link rel="subsection" type="application/atom+xml" '
        'href="/index.php/feed/all"/></entry></feed>')
EMPTY = '<feed xmlns="http://www.w3.org/2005/Atom"/>'


def session_with(handler):
    session = EbooksSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False,
    )
    session.connect()
    return session


@pytest.mark.parametrize("body,temporary", (
    ("<feed><unfinished", True),
    ("<html><body>PRIVATE_LOGIN_SSO</body></html>", True),
    ('<!DOCTYPE feed [<!ENTITY xx "PRIVATE">]><feed/>', False),
))
def test_root_protocol_failure_is_not_zero_or_security_bypass(body, temporary):
    calls = []
    def respond(req):
        calls.append(req.url.path)
        return httpx.Response(200, text=body)
    session = session_with(respond)
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
            session.search("Livre")
        assert calls == ["/index.php/feed"]
        expected = "SearchTemporarilyUnavailable" if temporary else None
        assert search_failure_code(caught.value) == expected
        assert public_service_unavailable(caught.value) is temporary
        assert caught.value.__cause__ is None
        assert "PRIVATE" not in str(caught.value)
    finally:
        session.close()


@pytest.mark.parametrize("body,temporary", (
    ("<feed><unfinished", True),
    ("<response><total>0</total></response>", True),
    (EMPTY, False),
))
def test_nested_books_feed_malformed_and_legitimate_empty_are_distinct(body, temporary):
    calls = []
    def respond(req):
        calls.append(req.url.path)
        return httpx.Response(
            200, text=ROOT if req.url.path == "/index.php/feed" else body,
        )
    session = session_with(respond)
    try:
        if temporary:
            with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
                session.search("")
            assert search_failure_code(caught.value) == "SearchTemporarilyUnavailable"
            assert caught.value.__cause__ is None
        else:
            assert session.search("") == []
            assert session.last_search_warnings == []
        assert calls == ["/index.php/feed", "/index.php/feed/all"]
    finally:
        session.close()
