"""Receipt of ordinary Numilog public transport, not an advanced API claim.

Replay reduced observed Oct 17 pages and the complete Oct 16 card fixture
through the real session + client with MockTransport. No live traffic.
"""
from pathlib import Path

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
import cifereca_numilog_public_client as client
from cifereca_numilog_public_provider import PLUGIN

FIXTURES = Path(__file__).with_name("fixtures")
EMPTY = "zzciferecaxxyyqqnobook20261017"


def session():
    s = PLUGIN.create_session(credential_id="__public__", label="Catalogue",
                              credentials={}, options={}, config={},
                              storage=None, diagnostics=None)
    s.connect()
    return s


def install_transport(monkeypatch, route):
    calls = []

    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "www.numilog.com"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        assert request.url.params["mode"] == "brSimple"
        calls.append(str(request.url))
        return route(request)

    def factory(*, producer, **kwargs):
        assert producer == "provider.numilog.public"
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", factory)
    return calls


def html(name):
    return httpx.Response(200, headers={"content-type": "text/html"},
                          content=(FIXTURES / name).read_bytes())


def test_two_observed_pages_use_exact_source_pager_and_do_not_claim_exhaustion(monkeypatch):
    def route(request):
        assert request.url.params["query"] == "loup"
        index = request.url.params.get("GRID_PAGE", "0")
        assert index in ("0", "1")
        return html("observed_search-loup-" + index + ".html")

    calls = install_transport(monkeypatch, route)
    s = session()
    try:
        p = s.browse_public_search("loup")
        q = s.follow_public_link(p.next_url)
        assert calls == [client.public_search_url("loup"), p.next_url]
        assert q.page_index == 1 and p.page_index == 0
        assert q.total_results == p.total_results == 973
        assert len(p.items) == len(q.items) == 1  # reduced observed excerpts
        assert p.items[0].record_id != q.items[0].record_id
        assert not p.complete and not q.complete
        assert q.next_url == client.public_search_url("loup", 2)
        refined = s.refine_loaded_pages((p, q), title="loup")
        assert refined.matching_count == 2 and refined.loaded_count == 2
        assert refined.public_unfiltered_total == 973
        assert not refined.public_results_fully_loaded
        assert refined.has_uninspected_or_unverified_matches
        assert all(not row.owned and not row.downloadable and row.download_url is None
                   for row in refined.items)
        assert len(calls) == 2
    finally:
        s.close()


@pytest.mark.parametrize("query,fixture,count,complete", [
    (EMPTY, "observed_search-empty.html", 0, True),
    ("jefferson", "public_search_jefferson_20261016.html", 20, False),
])
def test_real_public_envelope_carries_observed_zero_or_multiple(query, fixture,
                                                          count, complete, monkeypatch):
    calls = install_transport(monkeypatch, lambda request: html(fixture))
    s = session()
    try:
        response = s.search_with_response_facets(query)
        assert len(response.results) == len(response.public_notices) == count
        assert response.catalogue_complete is complete
        assert all(claim.result is row for row, claim in
                   zip(response.results, response.public_notices))
        assert calls == [client.public_search_url(query)]
    finally:
        s.close()


@pytest.mark.parametrize("status,code", [
    (401, "public_catalog_forbidden"), (403, "public_catalog_forbidden"),
    (429, "public_catalog_rate_limited"),
    (500, "public_catalog_unavailable"), (503, "public_catalog_unavailable"),
])
def test_failed_public_page_is_not_a_zero_or_private_fallback(status, code, monkeypatch):
    calls = install_transport(monkeypatch, lambda request: httpx.Response(status))
    s = session()
    try:
        with pytest.raises(ProviderError, match=code):
            s.search_with_response_facets("loup")
        assert calls == [client.public_search_url("loup")]
    finally:
        s.close()


@pytest.mark.parametrize("field", ["title", "author", "isbn", "publisher", "genre"])
def test_unattested_advanced_field_refused_before_transport(field, monkeypatch):
    calls = install_transport(monkeypatch, lambda request: pytest.fail("unexpected GET"))
    s = session()
    try:
        assert s.search_capabilities.fields == ("all",)
        with pytest.raises(ProviderCapabilityError, match="unsupported_field"):
            s.search_with_response_facets("loup", field=field)
        assert calls == []
    finally:
        s.close()
