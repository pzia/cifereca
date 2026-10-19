"""P1 #1917 — reconnect reopens a closed anonymous EbooksGratuits client.

MockTransport only: no live OPDS, accounts, actual EPUB, HTTP downloads
or suggestion that connect() proves service availability.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_ebooksgratuits import EbooksSession

HOST = "https://opds2.ebooksgratuits.org"
ROOT = HOST + "/index.php/feed"
ROOT_XML = """<feed xmlns="http://www.w3.org/2005/Atom">
<link rel="search" type="application/opensearchdescription+xml"
 href="/index.php/opensearch.xml"/></feed>"""
OSD = """<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
<Url type="application/atom+xml" template="/index.php/feed/search?q={searchTerms}"/>
</OpenSearchDescription>"""
BOOKS = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>urn:new:book</id><title>Livre nouveau hors cache</title>
<author><name>Autrice témoin</name></author>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="/index.php/fetch/new.epub"/>
</entry></feed>"""


def make_session(monkeypatch, *, backend="opds", refuse=False):
    requests, created = [], []
    result = EbooksSession(
        credential_id="__public__", label="EbooksGratuits",
        credentials={}, options={"acquisition_backend": backend},
        config={}, diagnostics=None, transport=None,
    )
    result.close()  # Mimic a Core lifecycle that reuses a closed session.

    def handle(request):
        requests.append(str(request.url))
        if backend == "html":
            assert request.url.path == "/ebooks.php"
            return httpx.Response(200, text="<html>Simulation HTML</html>",
                                  request=request)
        route = {
            "/index.php/feed": ROOT_XML,
            "/index.php/opensearch.xml": OSD,
            "/index.php/feed/search": BOOKS,
        }
        assert request.url.path in route, request.url.path
        return httpx.Response(200, text=route[request.url.path],
                              request=request)

    def factory():
        if refuse:
            raise OSError("PRIVATE_PATH")
        client = httpx.Client(
            transport=httpx.MockTransport(handle), trust_env=False,
        )
        created.append(client)
        return client

    monkeypatch.setattr(result, "_new_client", factory)
    return result, requests, created


def test_reconnect_restores_search_without_http_during_connect(monkeypatch):
    session, requests, created = make_session(monkeypatch)
    try:
        accounts = session.connect()
        assert len(accounts) == 1
        assert session.connected is True and not session.client.is_closed
        assert requests == []  # Local registration does NOT test OPDS.
        first_client = session.client

        first = session.search("fresh-book", field="all")
        assert [r.title for r in first] == ["Livre nouveau hors cache"]
        assert first[0].owned is False and first[0].downloadable is True
        assert first[0].download_url == HOST + "/index.php/fetch/new.epub"
        assert len(requests) == 3

        session.close()
        assert session.client.is_closed and session.connected is False
        assert session._accounts == []

        accounts = session.connect()
        assert len(accounts) == 1 and len(created) == 2
        assert session.client is not first_client
        assert first_client.is_closed and not session.client.is_closed
        assert len(requests) == 3  # Still no HTTP just because reconnect.

        again = session.search("fresh-book", field="all")
        assert [r.record_id for r in again] == [r.record_id for r in first]
        assert len(requests) == 6  # An actual new uncached OPDS search.
        assert all(request.startswith(HOST + "/") for request in requests)
        assert not again[0].owned
    finally:
        session.close()


def test_repeated_connect_without_close_does_not_replace_active_client(monkeypatch):
    session, requests, created = make_session(monkeypatch)
    try:
        session.connect()
        client = session.client
        session.connect()
        assert session.client is client
        assert len(created) == 1 and requests == []
    finally:
        session.close()


def test_failed_recreation_does_not_claim_connected_or_reach_network(monkeypatch):
    session, requests, created = make_session(monkeypatch, refuse=True)
    with pytest.raises(OSError, match="PRIVATE_PATH"):
        session.connect()
    assert not session.connected and session.client.is_closed
    assert session._accounts == []
    assert requests == [] and created == []


def test_html_simulation_backend_reconnect_keeps_independent_base_route(monkeypatch):
    session, requests, created = make_session(monkeypatch, backend="html")
    try:
        for run in range(2):
            session.connect()
            assert requests == []
            response = session._fetch("https://www.ebooksgratuits.com/ebooks.php")
            assert "Simulation HTML" in response.text
            session.close()
            assert len(requests) == run + 1
        assert len(created) == 2
        assert all("/ebooks.php" in url for url in requests)
        assert not any("opds2." in url for url in requests)
    finally:
        session.close()


def test_search_while_closed_is_refused_without_reopening_automatically(monkeypatch):
    session, requests, created = make_session(monkeypatch)
    with pytest.raises(ProviderError, match="connecter le catalogue"):
        session.search("fresh-book")
    assert created == [] and requests == []
    assert session.connected is False and session.client.is_closed
