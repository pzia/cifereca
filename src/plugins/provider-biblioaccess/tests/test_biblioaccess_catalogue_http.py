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
