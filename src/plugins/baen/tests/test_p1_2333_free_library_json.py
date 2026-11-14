"""#2333: Baen observed Free Library AJAX category (synthetic HTTP only).

No browser trace, actual product data, cookie, My Books, or network call.
"""
import json

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_baen import (
    BaenSession, FREE_JSON, FREE_CATEGORY_ID, FREE_JSON_PAGE_SIZE,
    UNVERIFIED_SEARCH_WARNING,
)


def session(handler):
    client = BaenSession(
        credential_id="public", label="Public", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    client.connect()
    return client


def payload(*, count=20):
    return {
        "returnCode": "single cat", "categoryId": FREE_CATEGORY_ID,
        "totalBooks": 82, "numOfBooks": count,
        "productData": [
            {
                "id": 20000 + i,
                "name": "SYNTHETIC TITLE " + str(i),
                "productUrl": "https://www.baen.com/synthetic-" + str(i) + ".html",
                "isFree": True,
                "isOwned": 0,
                "price": "Free Library Book",
            }
            for i in range(count)
        ],
    }


def test_page_two_uses_readonly_post_without_credential_or_inferred_rights():
    seen = []
    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert str(request.url) == FREE_JSON
        assert b"categoryId=2012" in request.content
        assert b"page=2" in request.content
        assert b"pageSize=20" in request.content
        assert b"showAvailable=false" in request.content
        assert b"q=" not in request.content
        assert "cookie" not in request.headers
        return httpx.Response(200, json=payload())
    current = session(handler)
    try:
        rows = current.search("", page=1)
        assert len(rows) == FREE_JSON_PAGE_SIZE == 20
        assert rows[0].title == "SYNTHETIC TITLE 0"
        assert rows[0].source_collection == "public"
        assert rows[0].metadata["metadata_evidence"] == "baen.free_library_category_json"
        assert all(not x.owned and not x.downloadable and x.download_url is None for x in rows)
        assert current.last_search_warnings == [UNVERIFIED_SEARCH_WARNING]
        assert len(seen) == 1
    finally:
        current.close()


def test_json_is_not_a_server_search_contract():
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, text='<a href="/allbooks/sample">Sample</a>')
        return httpx.Response(200, json=payload(count=1))
    client = session(handler)
    try:
        rows = client.search("Synthetic", page=2)
        assert len(rows) == 1
        assert requests[0].method == "GET"
        assert requests[0].url.params["q"] == "Synthetic"
        assert client.last_search_warnings == [UNVERIFIED_SEARCH_WARNING]
        client.search("", page=1)
        assert requests[1].method == "POST"
        assert b"q=" not in requests[1].content
    finally:
        client.close()


def test_json_partial_item_never_grants_ownership_or_false_zero():
    data = payload(count=3)
    data["productData"][1]["isFree"] = "true"
    data["productData"][2]["productUrl"] = (
        "https://evil.example/unsafe.html"
    )
    client = session(lambda r: httpx.Response(200, json=data))
    try:
        books = client.search("")
        assert len(books) == 1
        assert not books[0].owned and not books[0].downloadable
        assert client.last_search_warnings == [
            UNVERIFIED_SEARCH_WARNING,
            "partial_results : Baen : lignes de catalogue non qualifiées",
        ]
    finally:
        client.close()


@pytest.mark.parametrize("invalid", [
    {"returnCode": "single cat", "categoryId": "2012", "numOfBooks": 0},
    dict(payload(count=1), numOfBooks=2),
    dict(payload(count=1), categoryId="1717"),
    dict(payload(count=1), totalBooks="82"),
    dict(payload(count=1), productData={}),
    {"returnCode": "single cat", "categoryId": "2012",
     "totalBooks": 82, "numOfBooks": 0, "productData": []},
])
def test_incomplete_or_inconsistent_json_is_never_a_proven_global_zero(invalid):
    client = session(lambda r: httpx.Response(200, json=invalid))
    try:
        # Even a syntactically valid empty page never claims exhaustive q.
        if invalid.get("numOfBooks") == 0 and invalid.get("productData") == []:
            assert client.search("") == []
            assert client.last_search_warnings == [UNVERIFIED_SEARCH_WARNING]
        else:
            with pytest.raises(ProviderError, match="catalogue_schema_unverified"):
                client.search("")
    finally:
        client.close()


@pytest.mark.parametrize("status,expected", [
    (302, "public_redirect_unverified"),
    (403, "access_denied"),
    (429, "rate_limited"),
    (503, "service_unavailable"),
])
def test_json_route_refuses_redirects_and_upstream_errors(status, expected):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://www.baen.com/customer/account/login"}
            if status == 302 else {},
            text="SYNTHETIC PRIVATE RESPONSE SHOULD NOT LEAK",
        )
    client = session(handler)
    try:
        with pytest.raises(ProviderError, match=expected) as caught:
            client.search("")
        assert "SYNTHETIC PRIVATE" not in str(caught.value)
        assert len(requests) == 1
    finally:
        client.close()


@pytest.mark.parametrize("content_type,body", [
    ("text/html", b"<html>Login</html>"),
    ("application/json", b"not JSON"),
    ("application/json", b"x" * (256 * 1024 + 1)),
])
def test_http_200_without_qualified_json_is_refused_not_empty(content_type, body):
    client = session(lambda r: httpx.Response(
        200, content=body, headers={"content-type": content_type}))
    try:
        with pytest.raises(ProviderError, match="catalogue_schema_unverified"):
            client.search("")
    finally:
        client.close()


def test_owned_download_redirect_to_login_never_becomes_public_epub(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/synthetic.html":
            return httpx.Response(
                200, text='<a href="/download/index/owned/id/123">Download</a>')
        raise AssertionError("Unexpected request outside authorized detail GET")
    client = session(handler)
    # Transport unavailable: the generic owned download must not magically
    # gain a public EPUB entitlement just from a product/click label.
    try:
        from cifereca.models import SearchResult
        result = SearchResult(
            id="baen:fixture", record_id="fixture", docbase="BAEN",
            medium="digital", title="Synthetic", author=None, type="Livre numérique",
            format=None, publisher="Baen", published_at=None, description=None,
            thumbnail_url=None, scenario="BAEN_FREE_LIBRARY",
            source_provider="baen", source_credential="public",
            friendly_url="https://www.baen.com/synthetic.html",
        )
        with pytest.raises(ProviderError, match="aucun lien EPUB public"):
            client.download_direct(result, tmp_path)
        assert len(requests) == 1 and requests[0].method == "GET"
        assert not tmp_path.exists() or not list(tmp_path.iterdir())
    finally:
        client.close()
