"""BiblioAccess catalogue HTTPX contract, synthetic routes only."""
import importlib.util
from pathlib import Path
import sys
from urllib.parse import parse_qs

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load("biblioaccess_observations")
mod = load("biblioaccess_catalogue_http")


def client(handler):
    return mod.BiblioAccessCatalogueHTTP(transport=httpx.MockTransport(handler))


def test_redirect_and_two_catalog_pages_bounded_no_loan_inference():
    requests = []
    def handler(request):
        requests.append(str(request.url))
        assert request.method == "GET"
        assert request.url.host == "www.biblioaccess.com"
        assert request.url.path.startswith("/1409/Catalog/")
        p = request.url.path
        if p.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={"location":
                "/1409/Catalog/Search?query=La+Rochelle&type=all&pageindex=0"})
        page = int(request.url.params["pageindex"])
        assert page in (0, 1)
        if page == 0:
            return httpx.Response(200, headers={"content-type": "text/html"},
                text='<a href="/1409/Catalog/Book/123/title">Livre</a>'
                     '<a href="/1409/Catalog/Search?query=La+Rochelle&type=all&pageindex=1">Suiv</a>'
                     '<a href="/1717/Catalog/Book/777/title">Autre</a>')
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text='<a href="/1409/Catalog/Book/456/next">Livre suivant</a>')
    c = client(handler)
    try:
        result = c.search("La Rochelle", max_pages=2)
        assert result.book_ids == ("123", "456")
        assert result.pages_observed == 2
        assert result.status == "page_read_unverified"
        assert result.completeness == "partial"
        assert not result.authenticated and not result.entitlements_verified
        assert len(requests) == 3
    finally:
        c.close()


def test_budget_one_never_follows_second_page():
    requests = []
    def handler(request):
        requests.append(str(request.url))
        if "SearchResultSummary" in request.url.path:
            return httpx.Response(302, headers={"location":
                "/1409/Catalog/Search?query=x&pageindex=0"})
        assert request.url.params["pageindex"] == "0"
        return httpx.Response(200, headers={"content-type": "text/html"},
            text='<a href="/1409/Catalog/Book/1/">1</a>'
                 '<a href="/1409/Catalog/Search?query=x&pageindex=1">Suiv</a>')
    c = client(handler)
    try:
        result = c.search("x")
        assert result.book_ids == ("1",)
        assert result.status == "page_budget_exhausted"
        assert result.pages_observed == 1
        assert len(requests) == 2
    finally:
        c.close()


def test_summary_http_200_never_yields_false_zero_result():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html>Résumé sans livres</html>")
    c = client(handler)
    try:
        result = c.search("un livre")
        assert result.status == "summary_html_unverified"
        assert result.completeness == "partial"
        assert result.pages_observed == 0
        assert not result.entitlements_verified
        assert len(calls) == 1
    finally:
        c.close()


@pytest.mark.parametrize("location", [
    "https://evil.example/1409/Catalog/Search?query=x&pageindex=0",
    "/1717/Catalog/Search?query=x&pageindex=0",
    "/1409/Catalog/Search?query=other&pageindex=0",
    "/1409/Catalog/Search?query=x&pageindex=200",
    "/1409/Catalog/Search?query=x&pageindex=0&ticket=SYNTHETIC",
    "/1409/Catalog/Search?query=x&pageindex=0&pageindex=0",
])
def test_bad_redirect_is_never_followed(location):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": location})
    c = client(handler)
    try:
        with pytest.raises(mod.CatalogueHTTPError, match="redirect_refused"):
            c.search("x")
        assert len(calls) == 1
    finally:
        c.close()


@pytest.mark.parametrize(("response_status", "code"), [
    (401, "auth_required"), (403, "auth_required"),
    (429, "rate_limited"), (503, "remote_failure"),
])
def test_summary_http_errors_fail_closed(response_status, code):
    c = client(lambda _: httpx.Response(response_status))
    try:
        with pytest.raises(mod.CatalogueHTTPError, match=code):
            c.search("x")
    finally:
        c.close()


def test_redirected_html_non_200_is_not_parsed():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(302, headers={"location":
                "/1409/Catalog/Search?query=x&pageindex=0"})
        return httpx.Response(403)
    c = client(handler)
    try:
        with pytest.raises(mod.CatalogueHTTPError, match="auth_required"):
            c.search("x")
        assert len(calls) == 2
    finally:
        c.close()


def test_html_response_is_bounded_before_buffering():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=b"x" * (2_000_001))
    c = client(handler)
    try:
        with pytest.raises(mod.CatalogueHTTPError, match="body_limit"):
            c.search("test")
        assert len(calls) == 1
    finally:
        c.close()


def test_bad_query_budget_and_closed_client_no_network():
    c = client(lambda _: pytest.fail("unexpected I/O"))
    try:
        with pytest.raises(mod.CatalogueHTTPError, match="invalid_query"):
            c.search(" ")
        for budget in (0, 4, True, "3"):
            with pytest.raises(mod.CatalogueHTTPError, match="page_limit"):
                c.search("x", max_pages=budget)
    finally:
        c.close()
    with pytest.raises(mod.CatalogueHTTPError, match="client_closed"):
        c.search("test")


def test_detail_only_for_id_from_same_client_search():
    requests = []
    def handler(request):
        requests.append((request.method, str(request.url)))
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={
                "location": "/1409/Catalog/Search?query=roman&pageindex=0"})
        if request.url.path.endswith("/Catalog/Search"):
            return httpx.Response(200, headers={"content-type": "text/html"},
                text='<a href="/1409/Catalog/Book/123/example">Un roman</a>')
        assert request.url.path == "/1409/Catalog/Book/123/example"
        return httpx.Response(200, headers={"content-type": "text/html"},
            text='<script type="application/ld+json">'
                 '{"@context":"https://schema.org","@type":"Book",'
                 '"name":"Titre de test","url":"https://www.biblioaccess.com/1409/Catalog/Book/123",'
                 '"author":{"@type":"Person","name":"Auteur fictif"}}'
                 '</script>')
    c = client(handler)
    try:
        with pytest.raises(mod.CatalogueHTTPError, match="book_not_observed"):
            c.read_observed_notice("123")
        assert not requests
        observed = c.search("roman")
        assert observed.book_ids == ("123",)
        assert observed.book_urls == (("123", "https://www.biblioaccess.com/1409/Catalog/Book/123/example"),)
        with pytest.raises(mod.CatalogueHTTPError, match="book_not_observed"):
            c.read_observed_notice("456")
        with pytest.raises(mod.CatalogueHTTPError, match="book_id_refused"):
            c.read_observed_notice("../123")
        assert len(requests) == 2
        notice = c.read_observed_notice("123")
        assert notice is not None
        assert notice.book_id == "123"
        assert notice.title == "Titre de test"
        assert notice.author == "Auteur fictif"
        assert notice.entitlement == "unverified"
        assert notice.acquisition == "unsupported"
        assert len(requests) == 3
    finally:
        c.close()


def test_detail_unrecognized_html_returns_none_not_zero():
    seen = []
    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={
                "location": "/1409/Catalog/Search?query=x&pageindex=0"})
        if request.url.path.endswith("/Catalog/Search"):
            return httpx.Response(200, headers={"content-type": "text/html"},
                text='<a href="/1409/Catalog/Book/99/test">Livre</a>')
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<p>Unknown book metadata layout</p>")
    c = client(handler)
    try:
        c.search("x")
        assert c.read_observed_notice("99") is None
        assert len(seen) == 3
    finally:
        c.close()


def test_detail_redirect_no_follow_and_last_search_ids_replaced():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("SearchResultSummary"):
            q = request.url.params["query"]
            return httpx.Response(302, headers={
                "location": f"/1409/Catalog/Search?query={q}&pageindex=0"})
        if request.url.path.endswith("/Catalog/Search"):
            book = "123" if request.url.params["query"] == "first" else "456"
            return httpx.Response(200, headers={"content-type": "text/html"},
                text=f'<a href="/1409/Catalog/Book/{book}/test">Livre</a>')
        return httpx.Response(302, headers={"location":
            "https://evil.example/1409/Catalog/Book/123"})
    c = client(handler)
    try:
        c.search("first")
        c.search("second")
        with pytest.raises(mod.CatalogueHTTPError, match="book_not_observed"):
            c.read_observed_notice("123")
        assert len(calls) == 4
        with pytest.raises(mod.CatalogueHTTPError, match="book_unavailable"):
            c.read_observed_notice("456")
        assert len(calls) == 5
    finally:
        c.close()


def test_detail_uses_first_observed_slug_with_duplicate_links():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("SearchResultSummary"):
            return httpx.Response(302, headers={"location":
                "/1409/Catalog/Search?query=q&pageindex=0"})
        if request.url.path.endswith("/Catalog/Search"):
            return httpx.Response(200, headers={"content-type": "text/html"},
                text='<a href="/1409/Catalog/Book/88/correct-slug">Livre</a>'
                     '<a href="/1409/Catalog/Book/88/other-slug">Doublon</a>'
                     '<a href="/1717/Catalog/Book/91/foreign">Autre</a>')
        assert request.url.path == "/1409/Catalog/Book/88/correct-slug"
        return httpx.Response(200, headers={"content-type": "text/html"},
            text='<script type="application/ld+json">'
                 '{"@type":"Book","name":"Titre",'
                 '"url":"https://www.biblioaccess.com/1409/Catalog/Book/88/correct-slug"}'
                 '</script>')
    c = client(handler)
    try:
        result = c.search("q")
        assert result.book_urls == (
            ("88", "https://www.biblioaccess.com/1409/Catalog/Book/88/correct-slug"),)
        assert c.read_observed_notice("88").title == "Titre"
        assert len(calls) == 3
    finally:
        c.close()
