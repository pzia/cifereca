"""#1917: Gutenberg post-filters are page-local and OPDS failures are public-safe.

Only an httpx MockTransport is used; no network, accounts or acquisition.
"""
from types import SimpleNamespace as NS

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_gutenberg import GutenbergSession


FEED = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def make_session(status=200):
    calls = []

    def handle(request):
        calls.append(request)
        if status != 200:
            return httpx.Response(
                status, text="PRIVATE_SIGNED_URL?access_token=SECRET",
            )
        return httpx.Response(200, content=FEED)

    session = GutenbergSession(
        credential_id="public", label="", credentials={},
        options={"contact": "catalog-tests@example.test"},
        config={}, diagnostics=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handle))
    return session, calls


@pytest.mark.parametrize("field,scope,reason", (
    ("isbn", "all", "unsupported_field"),
    ("series", "all", "unsupported_field"),
    ("", "all", "unsupported_field"),
    ("all", "physical", "unsupported_scope"),
    ("all", "PRIVATE_SIGNED_QUERY", "unsupported_scope"),
    ("all", None, "unsupported_scope"),
))
def test_invalid_field_or_scope_rejected_before_http(field, scope, reason):
    session, calls = make_session()
    try:
        with pytest.raises(ProviderCapabilityError, match=reason) as caught:
            session.search("Writer", field=field, scope=scope)
        assert calls == []
        assert "PRIVATE_" not in str(caught.value)
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_author_filter_explicitly_discloses_only_requested_opds_page():
    session, calls = make_session()
    session._parse_feed = lambda *_: [
        NS(title="Sample", author="Jane Writer", metadata={}),
        NS(title="Other", author="Someone Else", metadata={}),
    ]
    try:
        rows = session.search("Writer", field="author")
        assert len(calls) == 1
        assert [row.title for row in rows] == ["Sample"]
        assert session.last_search_warnings == [
            "partial_results : filtre local limité à la page OPDS demandée",
        ]
        # A page-local miss is not proof of no matching books in the corpus.
        assert session.search("Absent", field="author") == []
        assert len(calls) == 2
        assert "partial_results" in session.last_search_warnings[0]
    finally:
        session.close()


def test_general_search_has_no_false_partial_warning():
    session, calls = make_session()
    session._parse_feed = lambda *_: [NS(title="Sample", author="Writer", metadata={})]
    try:
        assert len(session.search("Sample", field="all", scope="digital")) == 1
        assert len(calls) == 1
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_opds_transport_failure_has_stable_redacted_service_code():
    session, calls = make_session(status=503)
    try:
        with pytest.raises(ProviderError, match="service_unavailable") as caught:
            session.search("PRIVATE_QUERY", field="all")
        assert len(calls) == 1
        assert "PRIVATE" not in str(caught.value)
        assert "SECRET" not in str(caught.value)
        assert not session.last_search_warnings
    finally:
        session.close()



@pytest.mark.parametrize("status,code", (
    (401, "auth_required"),
    (403, "access_denied"),
    (429, "rate_limited"),
    (500, "service_unavailable"),
))
def test_opds_http_denial_and_throttle_are_typed_and_redacted(status, code):
    session, calls = make_session(status=status)
    try:
        with pytest.raises(ProviderError) as caught:
            session.search("query", field="all")
        assert str(caught.value) == (
            f"Project Gutenberg : recherche OPDS indisponible ({code})"
        )
        assert len(calls) == 1
        assert "PRIVATE" not in str(caught.value)
        assert "access_token" not in str(caught.value)
        assert session.last_search_warnings == []
    finally:
        session.close()
