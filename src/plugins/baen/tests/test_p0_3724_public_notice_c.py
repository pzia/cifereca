"""#3724/#6397: exact Baen category JSON -> anonymous C -> restart.

Recorded schema, synthetic product rows and HTTP only. No publisher call,
account session, detail crawl, or download is performed.
"""
from contextlib import closing
from types import SimpleNamespace

import httpx
import pytest

from cifereca_baen import BaenSession, FREE_JSON
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_web_choices import read_public_web_choices, resolve_public_web_choice
from cifereca.core.notice_cover_repository import evict_notice_cover, read_notice_cover
from cifereca.core.search_orchestration import _mirror_remote_results, _public_rows_from_response
from cifereca.storage import Storage


URL = "https://www.baen.com/synthetic-product.html"
IMAGE = "https://www.baen.com/media/catalog/product/public/cover.jpg"


def product(**changes):
    return {"id": 42, "name": "Synthetic Baen book", "productUrl": URL,
            "imageUrl": IMAGE, "isFree": True, "isOwned": 1, **changes}


def session(entries, *, cookie=False, credential="__public__", credentials=None):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, text='<a href="/allbooks/sample">Sample</a>')
        assert request.method == "POST" and str(request.url) == FREE_JSON
        assert b"categoryId=2012" in request.content and b"q=" not in request.content
        return httpx.Response(200, json={
            "returnCode": "single cat", "categoryId": "2012",
            "totalBooks": 82, "numOfBooks": len(entries), "productData": entries,
        })

    provider = BaenSession(credential_id=credential, label="Public",
                           credentials=credentials or {}, options={},
                           config={}, diagnostics=None, transport=None)
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False,
                                   headers={"cookie": "fixture=private"} if cookie else {})
    provider.connect()
    return provider, requests


@pytest.mark.parametrize("image", [IMAGE, None])
def test_observed_category_public_reference_is_durable(tmp_path, image):
    provider, requests = session([product(imageUrl=image)])
    filename = tmp_path / "baen.sqlite"
    try:
        assert provider.accounts == []
        response = provider.search_with_response_facets("", "catalog")
        assert response.catalogue_complete is False and not response.partial
        raw, = response.results
        declaration, = response.public_notices
        assert declaration.result is raw
        assert raw.thumbnail_url == image
        assert raw.source_collection == "public_catalog"
        assert raw.friendly_url == URL
        assert raw.owned is raw.downloadable is False
        assert raw.source_account_id is raw.download_url is raw.price is None
        assert raw.metadata == {"public_url_evidence": "baen.public_search_product_card"}
        row, = _public_rows_from_response(response, provider)
        with closing(Storage(filename)) as db:
            assert _mirror_remote_results(SimpleNamespace(storage=db), [row],
                                          identity_id="alice", require_public_cover_witness=True) == 1
            source = row._core_notice_cover_document_ref
            document = row._core_notice_canonical_cover_document_ref
            snapshot = read_consolidated_notice_cover(db, document)
            member, = snapshot.sources
            assert member.public_bibliography.record_id == raw.record_id
            ref, = snapshot.catalogue_web_references
            assert ref.url == URL and ref.access_mode == "public"
            assert tuple(c.url for c in snapshot.candidates) == ((image,) if image else ())
            menu = read_public_web_choices(db, document)
            choice, = menu.choices
            saved = read_notice_cover(db, source)
            assert evict_notice_cover(db, source, expected_epoch=saved.epoch)
            assert db.entitlements() == db.acquisitions() == db.placements() == []
        with closing(Storage(filename)) as db:
            assert read_notice_cover(db, source) is None
            hit, = search_public_notice_c(db, "Synthetic Baen book",
                                          group_by_document=True).hits
            assert hit.document == document
            assert read_public_web_choices(db, document) == menu
            assert resolve_public_web_choice(db, document, expected_epoch=menu.epoch,
                                             choice_id=choice.choice_id) == URL
            assert resolve_public_web_choice(db, document, expected_epoch=menu.epoch + 1,
                                             choice_id=choice.choice_id) is None
            assert db.entitlements() == db.acquisitions() == db.placements() == []
        legacy, = provider.search("")
        assert legacy.record_id == raw.record_id
        assert legacy.source_collection == "public"
        assert len(requests) == 2 and all(r.method == "POST" for r in requests)
    finally:
        provider.close()


@pytest.mark.parametrize("entries", [
    [product(id=True)], [product(id=None)],
    [product(productUrl="/foo/../synthetic-product.html")],
    [product(productUrl=URL + "?token=secret")],
    [product(), product(id=43, name=None)],
    [product(), product(productUrl="/other.html", name=None)],
])
def test_ambiguous_or_unqualified_product_does_not_mint_c(entries):
    provider, requests = session(entries)
    try:
        response = provider.search_with_response_facets("", "catalog")
        assert response.partial and response.catalogue_complete is False
        assert response.results == [] and response.public_notices == ()
        assert _public_rows_from_response(response, provider) == []
        assert len(requests) == 1
    finally:
        provider.close()


def test_rejected_product_does_not_hide_unrelated_public_book():
    entries = [product(), product(id=43, name=None),
               product(id=44, productUrl="/independent.html")]
    provider, requests = session(entries)
    try:
        response = provider.search_with_response_facets("", "catalog")
        assert response.partial
        raw, = response.results
        assert raw.friendly_url == "https://www.baen.com/independent.html"
        assert len(response.public_notices) == len(requests) == 1
    finally:
        provider.close()


@pytest.mark.parametrize("options,expected_calls", [
    ({"credential": "private"}, 0),
    ({"credentials": {"secret": "fixture"}}, 0),
    ({"cookie": True}, 1),
])
def test_nonanonymous_request_cannot_publish_public_c(options, expected_calls):
    provider, requests = session([product()], **options)
    try:
        response = provider.search_with_response_facets("", "catalog")
        assert response.partial and response.results == []
        assert response.public_notices == ()
        assert len(requests) == expected_calls
    finally:
        provider.close()


def test_legacy_query_and_unproven_epub_format_remain_partial():
    provider, requests = session([product()])
    try:
        result = provider.search_with_response_facets("Sample", "catalog")
        assert result.partial and result.catalogue_complete is False
        assert result.public_notices == () and len(result.results) == 1
        assert requests[0].method == "GET"
        exact = provider.search_with_response_facets("", "catalog", content_types=("epub",))
        assert exact.partial and not exact.results and not exact.public_notices
        assert len(requests) == 1
    finally:
        provider.close()
