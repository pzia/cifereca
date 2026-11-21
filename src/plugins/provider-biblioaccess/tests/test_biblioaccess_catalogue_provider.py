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


def test_public_envelope_retains_observed_titles_and_never_fallbacks():
    from cifereca.core.search_orchestration import _public_rows_from_response
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={
                "location": "/1409/Catalog/Search?query=roman&pageindex=0"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=(
            '<a href="/1409/Catalog/Book/123/example">Roman exemplaire</a>'
            '<a href="/1409/Catalog/Book/124/no-title"><img src="x"></a>'
            '<a href="/1409/Catalog/Book/125/ambiguous">A</a>'
            '<a href="/1409/Catalog/Book/125/ambiguous">B</a>'
            '<a href="/1409/Catalog/Book/126/cta">Voir la fiche</a>'))
    session, _ = _session(handler)
    try:
        session.connect()
        response = session.search_with_response_facets("roman", "catalog")
        assert response.partial is True and response.catalogue_complete is False
        assert len(response.results) == 4
        assert [n.result.record_id for n in response.public_notices] == ["123"]
        assert response.public_notices[0].result is response.results[0]
        assert all(row.owned is False and row.downloadable is False for row in response.results)
        projected, = _public_rows_from_response(response, session)
        assert projected.title == "Roman exemplaire"
        assert projected.friendly_url == "https://www.biblioaccess.com/1409/Catalog/Book/123/example"
        assert len(calls) == 2
    finally:
        session.close()


@pytest.mark.parametrize("body,expected", [
    ('<a href="/1409/Catalog/Book/123/example">Roman exemplaire</a>', 1),
    ('<p>Pas de carte reconnue</p>', 0),
])
def test_public_envelope_reports_window_not_exhaustive_catalog(body, expected):
    def handler(request):
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={
                "location": "/1409/Catalog/Search?query=roman&pageindex=0"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)
    session, _ = _session(handler)
    try:
        session.connect()
        response = session.search_with_response_facets("roman")
        assert len(response.results) == len(response.public_notices) == expected
        assert response.partial is (expected == 0) and response.catalogue_complete is False
        assert bool(session.last_search_warnings) is (expected == 0)
        clear = session.search_with_response_facets(" ")
        assert clear.catalogue_complete is None and not clear.public_notices
    finally:
        session.close()


def test_public_envelope_does_not_move_a_title_to_another_observed_url():
    def handler(request):
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={
                "location": "/1409/Catalog/Search?query=roman&pageindex=0"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=(
            '<a href="/1409/Catalog/Book/123/fiche"><img src="cover.png"></a>'
            '<a href="/1409/Catalog/Book/123/roman">Roman exemplaire</a>'
            '<a href="/1409/Catalog/Book/124/titre"><img src="cover.png"></a>'
            '<a href="/1409/Catalog/Book/124/titre">Roman exact</a>'
            '<a href="/1409/Catalog/Book/125/fiche">Premier titre</a>'
            '<a href="/1409/Catalog/Book/125/roman">Autre titre</a>'))

    session, _ = _session(handler)
    try:
        session.connect()
        response = session.search_with_response_facets("roman", "catalog")
        assert response.partial is True
        rows = {row.record_id: row for row in response.results}
        assert rows["123"].friendly_url.endswith("/123/fiche")
        assert rows["123"].metadata["bibliographic_evidence"] == "book_link"
        assert rows["125"].metadata["bibliographic_evidence"] == "book_link"
        notice, = response.public_notices
        assert notice.result is rows["124"]
        assert notice.result.title == "Roman exact"
    finally:
        session.close()


def test_real_federated_biblioaccess_c_survives_eviction(tmp_path):
    from cifereca.config import Config
    from cifereca.diagnostics import Diagnostics
    from cifereca.services import LibraryService
    from cifereca.storage import Storage
    from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
    from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
    from cifereca.core.notice_c_web_choices import read_public_web_choices, resolve_public_web_choice

    url = "https://www.biblioaccess.com/1409/Catalog/Book/123/example"
    calls = []
    def handler(request):
        calls.append(request.url.path)
        assert request.method == "GET" and "cookie" not in request.headers
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={
                "location": "/1409/Catalog/Search?query=Roman+exemplaire&pageindex=0"})
        assert request.url.path == "/1409/Catalog/Search"
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text='<a href="' + url + '">Roman exemplaire</a>')

    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "library"))
    config.set("identities", [{"id": "alice", "label": "Alice"}, {"id": "bob", "label": "Bob"}])
    config.set("providers.accounts", [])
    db = Storage(tmp_path / "notice.sqlite")
    service = LibraryService(config, db, Diagnostics())
    session, _ = _session(handler)
    session.connect()
    service._sessions = {session.provider_id: session}
    try:
        row, = service.search("Roman exemplaire", provider_id=session.provider_id, force=True)
        assert row.friendly_url == url and row.thumbnail_url is None
        assert row.source_collection == "public_catalog"
        assert row.owned is False and row.downloadable is False
        source_ref = row._core_notice_cover_document_ref
        document = row._core_notice_canonical_cover_document_ref
        source = read_notice_cover(db, source_ref)
        assert source.observation.status == "partial" and source.attribute.state == "pending"
        assert evict_notice_cover(db, source_ref, expected_epoch=source.epoch)
        service.select_identity("bob")
        reopened = Storage(tmp_path / "notice.sqlite")
        try:
            retained = read_consolidated_notice_cover(reopened, document)
            ref, = retained.catalogue_web_references
            assert (ref.url, ref.record_id, ref.provider_id) == (url, "123", session.provider_id)
            menu = read_public_web_choices(reopened, document)
            choice, = menu.choices
            assert resolve_public_web_choice(
                reopened, document, expected_epoch=menu.epoch, choice_id=choice.choice_id,
            ) == url
        finally:
            reopened.close()
        assert db.entitlements() == [] and db.acquisitions() == []
        assert len(calls) == 2
    finally:
        service.close()
        session.close()


def test_encoded_slug_retains_description_but_not_public_c_web_authority():
    from cifereca.core.search_orchestration import _public_rows_from_response
    url = "https://www.biblioaccess.com/1409/Catalog/Book/123/un-roman-%C3%A9"
    def handler(request):
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={
                "location": "/1409/Catalog/Search?query=roman&pageindex=0"})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text='<a href="' + url + '">Roman attesté</a>')
    session, _ = _session(handler)
    try:
        session.connect()
        response = session.search_with_response_facets("roman")
        row, = response.results
        assert row.friendly_url == url and row.title == "Roman attesté"
        assert len(response.public_notices) == 1
        projected, = _public_rows_from_response(response, session)
        assert projected.title == row.title and projected.record_id == row.record_id
        assert projected.friendly_url is None
        assert "public_url_evidence" not in projected.metadata
    finally:
        session.close()


@pytest.mark.parametrize("index", ["1", "100", "%C2%B2", "%D9%A1", "0000"])
def test_summary_must_begin_at_page_zero_before_catalogue_get(index):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        assert request.url.path.endswith("SearchResultSummary")
        return httpx.Response(302, headers={"location":
            "/1409/Catalog/Search?query=roman&pageindex=" + index})
    session, _ = _session(handler)
    try:
        session.connect()
        with pytest.raises(provider.ProviderError, match="catalogue_redirect_refused"):
            session.search_with_response_facets("roman")
        assert len(calls) == 1
    finally:
        session.close()


@pytest.mark.parametrize("target", [
    "&pageindex=1", "&type=all&pageindex=1&format=pdf",
    "&type=all&pageindex=1&language=fr",
    "&type=audio&pageindex=1",
])
def test_changed_filter_is_not_next_page(target):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={"location":
                "/1409/Catalog/Search?query=roman&type=all&pageindex=0"})
        assert request.url.params["pageindex"] == "0"
        return httpx.Response(200, headers={"content-type": "text/html"}, text=(
            '<a href="/1409/Catalog/Book/123/roman">Roman</a>'
            '<a rel="next" href="/1409/Catalog/Search?query=roman'
            + target.replace("&", "&amp;") + '">Suivant</a>'))
    session, _ = _session(handler)
    try:
        session.connect()
        assert session.search("roman", page=1) == []
        assert session.last_search_warnings
        assert len(calls) == 2
    finally:
        session.close()


# Synthetic markup: only the wording is reported by the #7162 field receipt.
# Do not treat these cases as a copy of the original remote HTML.
@pytest.mark.parametrize("body", [
    "<p>Aucun r\u00e9sultat pour la recherche roman</p>",
    "<div>Aucun r\u00e9sultat pour la recherche <strong>roman</strong></div>",
    "<p>Aucun r\u00e9sultat pour la recherche <span>roman</span></p>",
    "<html><head><title>Recherche</title></head><body>"
    "<p>Aucun r&eacute;sultat pour la recherche roman</p></body></html>",
])
def test_explicit_summary_zero_same_call_no_retry(body):
    url = http_adapter.search_summary_url("roman")
    assert http_adapter.summary_attests_zero(url, body)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.method == "GET"
        assert request.url.path == "/1409/Catalog/SearchResultSummary"
        assert request.url.params["query"] == "roman"
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content=body.encode("utf-8"))

    session, _ = _session(handler)
    try:
        session.connect()
        response = session.search_with_response_facets("roman")
        assert response.results == []
        assert response.public_notices == ()
        assert response.partial is False
        assert response.catalogue_complete is True
        assert not session.last_search_warnings
        assert len(calls) == 1
        assert session.search("roman", page=2) == []
        assert not session.last_search_warnings
        assert len(calls) == 2
    finally:
        session.close()


@pytest.mark.parametrize("body", [
    "<p>Aucun r\u00e9sultat pour la recherche autre</p>",
    "<p>Aucun r\u00e9sultat pour la recherche roman policier</p>",
    "<script>var message='<p>Aucun r\u00e9sultat pour la recherche roman</p>';</script>",
    "<div hidden><p>Aucun r\u00e9sultat pour la recherche roman</p></div>",
    "<template><p>Aucun r\u00e9sultat pour la recherche roman</p></template>",
    "<p>Aucun r\u00e9sultat pour la recherche roman</p><input type='password'>",
    "<p>Aucun r\u00e9sultat pour la recherche roman</p><form action='/Account/Login'></form>",
    "<p>Aucun r\u00e9sultat pour la recherche roman</p><a href='Book/123'>Livre</a>",
    "<p>Aucun r\u00e9sultat pour la recherche roman</p><a href='Search?pageindex=1'>Suite</a>",
    "<p>Aucun r\u00e9sultat pour la recherche roman</p><p>Aucun r\u00e9sultat pour la recherche autre</p>",
    "<p>Aucun r\u00e9sultat pour la recherche roman",
])
def test_summary_zero_refuses_ambiguous_or_unqualified_html(body):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content=body.encode("utf-8"))

    session, _ = _session(handler)
    try:
        session.connect()
        with pytest.raises(provider.ProviderError, match="search_unavailable"):
            session.search_with_response_facets("roman")
        assert len(calls) == 1
    finally:
        session.close()


@pytest.mark.parametrize("path", [
    "https://www.biblioaccess.com/1717/Catalog/SearchResultSummary?query=roman",
    "https://evil.example/1409/Catalog/SearchResultSummary?query=roman",
    "https://www.biblioaccess.com/1409/Catalog/SearchResultSummary?query=autre",
    "https://www.biblioaccess.com/1409/Catalog/SearchResultSummary?query=roman&query=roman",
    "https://www.biblioaccess.com/1409/Catalog/SearchResultSummary?query=roman&pageindex=1",
    "https://www.biblioaccess.com/1409/Catalog/Search?query=roman",
])
def test_summary_zero_requires_exact_tenant_query_and_route(path):
    assert not http_adapter.summary_attests_zero(
        path, "<p>Aucun r\u00e9sultat pour la recherche roman</p>")
