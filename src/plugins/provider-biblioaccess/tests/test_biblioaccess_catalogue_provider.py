"""Real Core search/detail contract over a synthetic BiblioAccess HTTP server."""
import importlib.util
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("biblioaccess_observations")
_load("biblioaccess_metadata")
http_adapter = _load("biblioaccess_catalogue_http")
provider = _load("cifereca_biblioaccess_catalogue")


def _session(handler):
    c = http_adapter.BiblioAccessCatalogueHTTP(transport=httpx.MockTransport(handler))
    session = provider.LaRochelleCatalogueSession(
        credential_id="__public__", label="La Rochelle", credentials={}, options={},
        client_factory=lambda: c,
    )
    return session, c


def test_public_catalogue_is_registered_without_a_reader_or_credentials():
    d = provider.DESCRIPTOR
    assert d.id == "biblioaccess-catalogue.la-rochelle"
    assert d.capabilities.search and d.capabilities.holdings
    assert not d.capabilities.authentication and not d.capabilities.loans
    assert d.search_capabilities.pagination == "page"
    assert d.search_capabilities.supports_scope("digital")
    assert not d.search_capabilities.supports_field("author")
    assert provider.PLUGIN.descriptor is d


def test_search_result_links_titles_and_detail_mapping_without_authentication():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path,
                      request.headers.get("authorization")))
        assert "cookie" not in request.headers
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(
                302, headers={"location":
                "/1409/Catalog/Search?query=roman&pageindex=0"})
        if request.url.path.endswith("/Catalog/Search"):
            return httpx.Response(
                200, headers={"content-type": "text/html"},
                text='<a href="/1409/Catalog/Book/123/un-roman">'
                     '<span>Roman synthétique</span></a>'
                     '<a href="https://evil.example/1409/Catalog/Book/666/x">'
                     'Intrus</a>')
        assert request.url.path == "/1409/Catalog/Book/123/un-roman"
        return httpx.Response(
            200, headers={"content-type": "text/html"},
            text='<script type="application/ld+json">'
            '{"@type":"Book","name":"Roman synthétique",'
            '"author":{"name":"Auteur fictif"},'
            '"url":"https://www.biblioaccess.com/1409/Catalog/Book/123"}'
            '</script>')
    session, client = _session(handler)
    try:
        assert session.connect() == [] and session.connected
        matches = session.search("roman", scope="digital")
        assert len(matches) == 1
        row = matches[0]
        assert row.title == "Roman synthétique"
        assert row.record_id == "123"
        assert row.source_provider == provider.PROVIDER_ID
        assert row.source_credential == "__public__"
        assert row.owned is False and row.downloadable is False
        assert row.download_url is None and row.medium == "digital"
        assert row.friendly_url.endswith("/1409/Catalog/Book/123/un-roman")
        detail = session.holdings(row)
        assert detail.title == "Roman synthétique"
        assert detail.author == "Auteur fictif"
        assert detail.holdings == [] and detail.availability_status == "unknown"
        assert len(calls) == 3
        assert all(method == "GET" and auth is None for method, _, auth in calls)
    finally:
        session.close()
    assert not session.connected
    assert not client.http.cookies


def test_public_catalogue_page_one_uses_the_actual_next_link_not_page_zero():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if "SearchResultSummary" in request.url.path:
            return httpx.Response(302, headers={
                "location":"/1409/Catalog/Search?query=alpha&pageindex=0"})
        idx = request.url.params["pageindex"]
        if idx == "0":
            return httpx.Response(200, headers={"content-type": "text/html"},
                text='<a href="/1409/Catalog/Book/100/old">Page 0</a>'
                     '<a href="/1409/Catalog/Search?query=alpha&pageindex=1">Suivant</a>')
        assert idx == "1"
        return httpx.Response(200, headers={"content-type": "text/html"},
            text='<a href="/1409/Catalog/Book/200/new">Page 1</a>')
    session, client = _session(handler)
    try:
        session.connect()
        matches = session.search("alpha", page=1)
        assert [x.record_id for x in matches] == ["200"]
        assert matches[0].title == "Page 1"
        assert len(calls) == 3
    finally:
        session.close()


def test_search_rejects_private_or_unsupported_requests_before_network():
    calls = []
    session, client = _session(lambda _: calls.append(1) or httpx.Response(200))
    try:
        with pytest.raises(provider.ProviderError, match="not_connected"):
            session.search("romans")
        session.connect()
        for kwargs in (
            {"field":"author"}, {"scope":"physical"},
            {"page": -1}, {"page": True}, {"page": 101},
            {"query": object()}, {"query": "x" * 513},
        ):
            value = kwargs.pop("query", "roman")
            with pytest.raises(provider.ProviderCapabilityError):
                session.search(value, **kwargs)
        assert session.search(" ") == []
        assert calls == []
    finally:
        session.close()


def test_detail_of_another_search_and_another_credential_is_refused():
    from dataclasses import replace
    from cifereca.plugin_api import SearchResult
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={"location":
                "/1409/Catalog/Search?query=alpha&pageindex=0"})
        return httpx.Response(200, headers={"content-type":"text/html"},
            text='<a href="/1409/Catalog/Book/123/example">Example</a>')
    session, client = _session(handler)
    try:
        session.connect()
        results = session.search("alpha")
        assert len(results) == 1
        with pytest.raises(provider.ProviderCapabilityError, match="item_unbound"):
            session.holdings(replace(results[0], source_credential="reader-123"))
        with pytest.raises(provider.ProviderError, match="book_not_observed"):
            session.holdings(replace(results[0], record_id="456"))
        assert len(calls) == 2
    finally:
        session.close()


def test_public_search_without_redirect_does_not_fake_empty_catalogue():
    session, client = _session(
        lambda _: httpx.Response(200, headers={"content-type":"text/html"},
                                 text="<html>Page de recherche inconnue</html>"))
    try:
        session.connect()
        with pytest.raises(provider.ProviderError, match="search_unavailable"):
            session.search("roman")
    finally:
        session.close()
