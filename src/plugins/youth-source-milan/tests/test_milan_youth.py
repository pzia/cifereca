import httpx
import pytest

from milan_youth import (
    CATALOG,
    MilanYouthSource,
    allowed_url,
    listing_products,
    listing_url,
    next_cursor,
    normalize_isbn,
    parse_detail,
    parse_published_age,
)


LISTING = """
<html><body>
  <a href="/livres/63047-moqueuse/">Moqueuse</a>
  <a href="/livres/63047-moqueuse/">Lire la suite</a>
  <a href="/livres/77561-lescargot/">L'escargot</a>
  <a href="/livres/99999-adulte/">Essai adulte</a>
  <a href="/livres/page/2/">2</a>
  <a href="https://evil.example/livres/1-bad/">Bad</a>
</body></html>
"""

MOQUEUSE = """
<html><body>
  <h1>Moqueuse</h1>
  <div>Auteur</div><div>Nathalie Somers</div>
  <div>Collection</div><div>Premiers romans 7 - 9 ans</div>
  <div>Thèmes</div><div>amitié, entraide, harcèlement scolaire</div>
  <div>Âge</div><div>À partir de 8 ans</div>
  <section><div>Code ISBN</div><div>978-2-408-02080-4</div></section>
</body></html>
"""

ESCARGOT = """
<html><body>
  <h1>L'escargot</h1>
  <div>Illustrateur</div><div>Mélisande Luthringer</div>
  <div>Collection</div><div>Mes animaux à toucher</div>
  <div>Âge</div><div>À partir de 1 ans</div>
  <div>Code ISBN</div><div>978-2-408-04507-4</div>
</body></html>
"""

ADULT = """
<html><body>
  <h1>Livre adulte</h1>
  <div>Âge</div><div>À partir de 18 ans</div>
  <div>Code ISBN</div><div>978-2-408-02080-4</div>
</body></html>
"""


def test_published_age_is_explicit_and_bounded_under_18():
    age = parse_published_age("À partir de 8 ans")
    assert (age.age_min, age.label) == (8, "À partir de 8 ans")
    assert parse_published_age("À partir de 1 ans").age_min == 1
    assert parse_published_age("À partir de 18 ans") is None
    assert parse_published_age("Premiers romans 7 - 9 ans") is None


def test_listing_extracts_product_links_and_filters_locally():
    assert listing_products(LISTING) == (
        ("Moqueuse", "https://www.editionsmilan.com/livres/63047-moqueuse/"),
        ("L'escargot", "https://www.editionsmilan.com/livres/77561-lescargot/"),
        ("Essai adulte", "https://www.editionsmilan.com/livres/99999-adulte/"),
    )
    assert listing_products(LISTING, text_filter="escargot") == (
        ("L'escargot", "https://www.editionsmilan.com/livres/77561-lescargot/"),
    )



def test_listing_recognizes_accessible_cover_only_links_without_inventing_titles():
    listing = """
      <section>
        <a href="/livres/63047-moqueuse/">
          <picture><img loading="lazy" alt="Couverture de Moqueuse"></picture>
        </a>
        <a href="/livres/77561-lescargot/" aria-label="L'escargot">
          <svg aria-hidden="true"></svg>
        </a>
        <a href="/livres/99999-adulte/" title="Essai adulte"></a>
        <a href="/livres/63047-moqueuse/">Moqueuse — roman jeunesse</a>
        <a href="https://evil.example/livres/1-bad/">
          <img alt="Couverture de faux livre">
        </a>
        <a href="/livres/collections/99-truc/"><img alt="Collection sans livre"></a>
        <a href="/livres/page/2/" aria-label="Page suivante"></a>
      </section>
    """
    assert listing_products(listing) == (
        ("Moqueuse — roman jeunesse", "https://www.editionsmilan.com/livres/63047-moqueuse/"),
        ("L'escargot", "https://www.editionsmilan.com/livres/77561-lescargot/"),
        ("Essai adulte", "https://www.editionsmilan.com/livres/99999-adulte/"),
    )
    assert listing_products(listing, text_filter="escargot") == (
        ("L'escargot", "https://www.editionsmilan.com/livres/77561-lescargot/"),
    )
    assert next_cursor(listing, current_page=1) == "2"


def test_cover_only_listing_collects_real_details_with_age_isbn_and_request_limit():
    listing = (
        '<a href="/livres/63047-moqueuse/">'
        '<img alt="Couverture de Moqueuse"></a>'
        '<a href="/livres/77561-lescargot/" title="L\'escargot"></a>'
        '<a href="/livres/page/2/">Suivant</a>'
    )
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/livres/":
            html = listing
        elif request.url.path == "/livres/63047-moqueuse/":
            html = MOQUEUSE
        elif request.url.path == "/livres/77561-lescargot/":
            html = ESCARGOT
        else:
            raise AssertionError(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.browse(age=8, limit=2)
    assert [row["title"] for row in result["items"]] == ["Moqueuse", "L'escargot"]
    assert [row["isbn13"] for row in result["items"]] == [
        "9782408020804", "9782408045074",
    ]
    assert result["next_cursor"] == "2"
    assert seen == ["/livres/", "/livres/63047-moqueuse/", "/livres/77561-lescargot/"]


def test_paginated_catalogue_without_recognized_books_is_not_silent_empty_success():
    listing = '<div><a href="/livres/page/2/">2</a></div>'
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text=listing
        )
    ))
    with pytest.raises(RuntimeError, match="no recognizable book links"):
        MilanYouthSource(client=client).browse(age=8, limit=2)
    # A genuinely empty non-paginated listing remains an empty successful response.
    empty = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>Aucun résultat</p>"
        )
    ))
    assert MilanYouthSource(client=empty).browse(age=8, limit=2)["items"] == ()


def test_generic_milan_product_labels_keep_verified_product_urls():
    listing = """
      <a href="/livres/63047-moqueuse/">Lire la suite</a>
      <a href="/livres/77561-lescargot/">Voir le livre</a>
      <a href="/livres/63047-moqueuse/">Moqueuse</a>
      <a href="https://evil.example/livres/1-bad/">Voir le livre</a>
      <a href="/livres/page/2/">Voir la suite</a>
    """
    diagnostic = {}
    assert listing_products(listing, listing_diagnostic=diagnostic) == (
        ("Moqueuse", "https://www.editionsmilan.com/livres/63047-moqueuse/"),
        ("lescargot", "https://www.editionsmilan.com/livres/77561-lescargot/"),
    )
    assert listing_products(listing, text_filter="escargot") == (
        ("lescargot", "https://www.editionsmilan.com/livres/77561-lescargot/"),
    )
    assert diagnostic["generic_product_label_count"] == 2
    assert diagnostic["recognized_product_count"] == 2
    assert diagnostic["product_href_count"] == 3


def test_generic_milan_cards_qualify_only_detail_verified_age_and_isbn():
    listing = """
      <a href="/livres/63047-moqueuse/">Voir le livre</a>
      <a href="/livres/77561-lescargot/">Lire la suite</a>
      <a href="/livres/99999-adulte/">Voir le livre</a>
      <a href="/livres/page/2/">Suivant</a>
    """
    seen = []

    def handler(request):
        seen.append(request.url.path)
        path = request.url.path
        data = (
            listing if path == "/livres/"
            else MOQUEUSE if "63047" in path
            else ESCARGOT if "77561" in path
            else ADULT
        )
        return httpx.Response(200, headers={"content-type": "text/html"}, text=data)

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.browse(age=8, limit=2)
    assert [item["title"] for item in result["items"]] == ["Moqueuse", "L'escargot"]
    assert [item["isbn13"] for item in result["items"]] == [
        "9782408020804", "9782408045074",
    ]
    assert result["next_cursor"] == "2"
    assert source.diagnostic_snapshot()["generic_product_label_count"] == 3
    assert source.diagnostic_snapshot()["detail_requests"] == 2
    assert seen == ["/livres/", "/livres/63047-moqueuse/", "/livres/77561-lescargot/"]


def test_detail_requires_explicit_age_and_valid_isbn():
    row = parse_detail(
        MOQUEUSE,
        url="https://www.editionsmilan.com/livres/63047-moqueuse/",
        fallback_title="Fallback",
    )
    assert row is not None
    assert row["title"] == "Moqueuse"
    assert row["authors"] == ("Nathalie Somers",)
    assert row["isbn13"] == "9782408020804"
    assert row["age_ranges"][0] == {
        "age_min": 8,
        "age_max": None,
        "age_kind": "publisher",
        "source_label": "À partir de 8 ans",
        "source_id": "milan",
        "confidence": 1.0,
    }
    assert row["metadata"]["age_evidence"] == "milan.product.explicit"
    assert row["metadata"]["collection"] == "Premiers romans 7 - 9 ans"


def test_detail_does_not_infer_age_from_collection():
    html = """
      <h1>Sans âge explicite</h1>
      <div>Collection</div><div>Littérature 9-12 ans</div>
      <div>Code ISBN</div><div>978-2-408-02080-4</div>
    """
    assert parse_detail(
        html,
        url="https://www.editionsmilan.com/livres/1-sans-age/",
    ) is None


def test_adult_age_is_rejected():
    assert parse_detail(
        ADULT,
        url="https://www.editionsmilan.com/livres/99999-adulte/",
    ) is None


def test_invalid_isbn_or_external_product_is_rejected():
    bad_isbn = """
      <h1>Bad</h1><div>Âge</div><div>À partir de 8 ans</div>
      <div>Code ISBN</div><div>9780000000000</div>
    """
    assert parse_detail(
        bad_isbn,
        url="https://www.editionsmilan.com/livres/1-bad/",
    ) is None
    assert parse_detail(
        MOQUEUSE,
        url="https://evil.example/livres/63047-moqueuse/",
    ) is None


def test_listing_pagination_requires_actual_next_page_link():
    assert next_cursor(LISTING, current_page=1) == "2"
    assert next_cursor("<a href='/livres/page/3/'>3</a>", current_page=1) == ""
    assert next_cursor("<p>Fin</p>", current_page=1) == ""
    assert listing_url(page=1) == CATALOG
    assert listing_url(page=500).endswith("/livres/page/100/")


def test_source_browse_fetches_at_most_requested_matching_products():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/livres/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=LISTING,
            )
        if "63047-moqueuse" in request.url.path:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=MOQUEUSE)
        if "77561-lescargot" in request.url.path:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=ESCARGOT)
        if "99999-adulte" in request.url.path:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=ADULT)
        raise AssertionError(str(request.url))

    source = MilanYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = source.browse(limit=2)
    assert [row["title"] for row in result["items"]] == ["Moqueuse", "L'escargot"]
    assert result["next_cursor"] == "2"
    assert result["fresh_for_seconds"] == 86400
    assert len(seen) == 3


def test_age_filter_is_local_after_attested_product_age():
    def handler(request):
        if request.url.path == "/livres/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=LISTING)
        if "63047-moqueuse" in request.url.path:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=MOQUEUSE)
        if "77561-lescargot" in request.url.path:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=ESCARGOT)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=ADULT)

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.browse(age=4, limit=8)
    assert [row["title"] for row in result["items"]] == ["L'escargot"]


def test_search_filters_listing_titles_locally_without_remote_search_param():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/livres/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=LISTING)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=MOQUEUSE)

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.browse(text_filter="moqueuse", cursor="1", limit=8)
    assert [row["title"] for row in result["items"]] == ["Moqueuse"]
    assert seen[0].url.path == "/livres/"
    assert not seen[0].url.query


def test_invalid_cursor_fails_before_network():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, text=LISTING)

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError):
        source.browse(cursor="bad")
    with pytest.raises(ValueError):
        source.browse(cursor="101")
    assert calls == 0


def test_http_mime_and_size_fail_explicitly():
    failed = MilanYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(503, headers={"content-type": "text/html"}, text="down")
        ))
    )
    with pytest.raises(RuntimeError, match="503"):
        failed.search("x")

    non_html = MilanYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={})
        ))
    )
    with pytest.raises(RuntimeError, match="not HTML"):
        non_html.search("x")

    oversized = MilanYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"x" * 2_000_001,
            )
        ))
    )
    with pytest.raises(RuntimeError, match="2 MB"):
        oversized.search("x")


def test_source_info_and_url_security():
    source = MilanYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text="")
        ))
    )
    assert source.source_info() == {
        "source_id": "milan",
        "display_name": "Éditions Milan",
        "homepage_url": CATALOG,
    }
    assert allowed_url("https://www.editionsmilan.com/livres/")
    assert not allowed_url("https://user:pass@www.editionsmilan.com/livres/")
    assert not allowed_url("https://www.editionsmilan.com:444/livres/")
    assert normalize_isbn("978-2-408-02080-4") == "9782408020804"



def test_detail_inspection_is_hard_bounded_to_eight_pages():
    links = "".join(
        f'<a href="/livres/{10000+i}-book-{i}/">Book {i}</a>'
        for i in range(12)
    )
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/livres/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=links)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<h1>Book</h1><div>Collection</div><div>9-12 ans</div>",
        )

    source = MilanYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = source.search("", limit=8)
    assert result["items"] == ()
    assert len(calls) == 9  # listing + 8 product pages


def test_redirect_response_is_rejected_not_followed():
    source = MilanYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={
                    "content-type": "text/html",
                    "location": "https://evil.example/",
                },
                text="redirect",
            )
        ))
    )
    with pytest.raises(RuntimeError, match="302"):
        source.search("x")

@pytest.mark.parametrize("invalid_age", (-1, 18, 100, "invalid", "9.5"))
def test_invalid_age_is_rejected_before_any_http_request(invalid_age):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>Empty</p>")

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="age must"):
        source.browse(age=invalid_age)
    assert seen == []


def test_milan_page_pipeline_into_real_youth_cache_survives_outages():
    """Real age-aware UI fetch + Milan parser + YouthCatalog cache; network mocked."""
    from pathlib import Path
    import sys

    catalog_dir = Path(__file__).resolve().parents[2] / "youth-catalog"
    if str(catalog_dir) not in sys.path:
        sys.path.insert(0, str(catalog_dir))
    from youth_catalog import YouthCatalogPlugin
    from youth_page import fetch_source_page, page_parts

    state = {"fault": "", "requests": []}

    def handler(request):
        assert request.url.host == "www.editionsmilan.com"
        state["requests"].append(str(request.url))
        if state["fault"] == "http503":
            return httpx.Response(503, headers={"content-type": "text/html"}, text="offline")
        if state["fault"] == "timeout":
            raise httpx.ReadTimeout("offline", request=request)
        if request.url.path == "/livres/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=(
                '<a href="/livres/63047-moqueuse/">Moqueuse</a>'
                '<a href="/livres/page/2/">Suivant</a>'
            ))
        if request.url.path == "/livres/page/2/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=(
                '<a href="/livres/77561-lescargot/">L escargot</a>'
            ))
        if request.url.path == "/livres/63047-moqueuse/":
            html = MOQUEUSE
            if state["fault"] == "recovered":
                html = html.replace("<h1>Moqueuse</h1>", "<h1>Moqueuse retrouvée</h1>")
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        if request.url.path == "/livres/77561-lescargot/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=ESCARGOT)
        raise AssertionError(f"unexpected Milan URL: {request.url}")

    source = MilanYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    )
    catalog = YouthCatalogPlugin()
    assert catalog.search_cached("camille") == ()
    assert state["requests"] == []

    first = fetch_source_page(source, "", age=8, themes=(), cursor=None, limit=8)
    rows, cursor, ttl = page_parts(first)
    assert cursor == "2" and ttl == 86400
    report = catalog.ingest("camille", "milan", rows, fresh_for_seconds=ttl)
    assert (report.accepted, report.rejected) == (1, 0)
    book = catalog.search_cached("camille", age=8, age_kind="publisher")[0]
    assert book.title == "Moqueuse"
    assert book.identifiers["isbn"] == "9782408020804"
    assert book.provenance == ("milan",)
    assert book.age_ranges[0].source_id == "milan"
    assert book.age_ranges[0].source_label == "À partir de 8 ans"
    assert catalog.search_cached("alex") == ()

    second = fetch_source_page(source, "", age=None, themes=(), cursor=cursor, limit=8)
    rows2, cursor2, ttl2 = page_parts(second)
    assert cursor2 == ""
    assert catalog.ingest("camille", "milan", rows2, replace_source=False, fresh_for_seconds=ttl2).accepted == 1
    assert {b.title for b in catalog.search_cached("camille")} == {"Moqueuse", "L'escargot"}
    prior = catalog.search_cached("camille")
    fresh_before = catalog.index.fresh_until("camille", "milan")
    calls_before = len(state["requests"])
    assert fresh_before
    assert {b.title for b in catalog.search_cached("camille", age=8)} == {"Moqueuse", "L'escargot"}
    assert len(state["requests"]) == calls_before  # cache reads stay local

    for fault, error in (("http503", RuntimeError), ("timeout", httpx.ReadTimeout)):
        state["fault"] = fault
        with pytest.raises(error):
            fetch_source_page(source, "", age=8, themes=(), cursor=None, limit=8)
        assert catalog.search_cached("camille") == prior
        assert catalog.index.fresh_until("camille", "milan") == fresh_before
        assert catalog.search_cached("alex") == ()

    state["fault"] = "recovered"
    recovered = fetch_source_page(source, "", age=8, themes=(), cursor=None, limit=8)
    records, _, fresh = page_parts(recovered)
    assert catalog.ingest("camille", "milan", records, replace_source=True, fresh_for_seconds=fresh).accepted == 1
    assert [b.title for b in catalog.search_cached("camille", age=8)] == ["Moqueuse retrouvée"]
    assert catalog.search_cached("alex") == ()
    assert len(state["requests"]) == calls_before + 4  # 2 failures + listing and detail


def test_milan_collection_reports_only_sanitized_structure_through_plugin_context():
    from types import SimpleNamespace
    import json

    listing = (
        '<a href="/livres/63047-moqueuse/"><img alt="Couverture de Moqueuse"></a>'
        '<a href="/livres/page/2/">Suivant</a>'
        '<a href="/livres/collections/recents/">Voir les collections</a>'
        '<a href="https://evil.example/other/path?secret=ABC">Mauvais domaine</a>'
    )

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              text=listing if request.url.path == "/livres/" else MOQUEUSE)

    sent = []
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.activate(SimpleNamespace(report_diagnostic=lambda *args, **kwargs: sent.append((args, kwargs)))) is source
    result = source.browse(age=8, limit=2)
    assert len(result["items"]) == 1
    diag = source.diagnostic_snapshot()
    assert diag["http_status"] == 200
    assert diag["content_type_is_html"] is True
    assert diag["html_bytes"] > 0 and len(diag["html_sha256_prefix"]) == 16
    assert diag["anchor_count"] == 4
    assert diag["milan_catalogue_href_count"] == 3
    assert diag["product_href_count"] == 1
    assert diag["product_link_accessible_count"] == 1
    assert diag["unrecognized_catalogue_href_count"] == 2
    assert diag["recognized_product_count"] == 1
    assert diag["next_cursor_present"] is True
    assert diag["detail_requests"] == 1 and diag["qualified_items"] == 1
    assert diag["phase"] == "complete"
    assert sent and sent[-1][0] == ("Milan catalogue collection", "ok")
    assert sent[-1][1]["level"] == "normal"
    assert sent[-1][1]["detail"] == diag
    payload = json.dumps(diag)
    assert "Moqueuse" not in payload
    assert "evil.example" not in payload
    assert "secret=ABC" not in payload
    assert "/livres/63047" not in payload


def test_milan_diagnostic_survives_http_error_and_paginated_parser_failure():
    from types import SimpleNamespace

    sent = []
    context = SimpleNamespace(report_diagnostic=lambda *args, **kwargs: sent.append((args, kwargs)))
    failure = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(429, headers={"content-type": "text/html"}, text="denied")
    )))
    failure.activate(context)
    with pytest.raises(RuntimeError, match="429"):
        failure.browse(age=8)
    detail = failure.diagnostic_snapshot()
    assert detail["http_status"] == 429
    assert detail["phase"] == "listing"
    assert detail["error_type"] == "RuntimeError"
    assert sent[-1][0] == ("Milan catalogue collection", "error")

    empty = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/html"},
            text='<a href="/livres/page/2/">Suivant</a>',
        )
    )))
    empty.activate(context)
    with pytest.raises(RuntimeError, match="no recognizable book links") as error:
        empty.browse(age=8)
    message = str(error.value)
    assert "anchors=1" in message
    assert "milan_links=1" in message
    assert "product_links=0" in message
    assert "html_sha256=" in message
    assert "/livres/" not in message
    detail = empty.diagnostic_snapshot()
    assert detail["anchor_count"] == 1
    assert detail["product_href_count"] == 0
    assert detail["next_cursor_present"] is True
    assert detail["detail_requests"] == 0
    assert detail["error_type"] == "InvalidCatalogueStructureError"
    assert detail["state"] == "invalid"
    assert error.value.collection_request_count == 1
    assert sent[-1][0] == ("Milan catalogue collection", "error")


def test_milan_diagnostic_callback_failure_does_not_block_collecting():
    from types import SimpleNamespace

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text="<p>Aucun livre</p>")
    )))
    source.activate(SimpleNamespace(report_diagnostic=lambda *args, **kwargs: 1 / 0))
    assert source.browse(age=8)["items"] == ()
    assert source.diagnostic_snapshot()["phase"] == "complete"


def test_http_error_preserves_typed_status_for_youth_diagnostics():
    """A single mocked HTTP request; no remote publisher contacted."""
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            429, headers={"content-type": "text/html"}, text="private body",
        )
    )))
    with pytest.raises(RuntimeError, match="HTTP 429") as raised:
        source.browse(age=8)
    assert raised.value.http_status == 429




def test_milan_per_collection_http_counter_includes_listing_and_product_details():
    requests = []
    def handler(req):
        requests.append(req.url.path)
        text = LISTING if req.url.path == "/livres/" else MOQUEUSE
        return httpx.Response(200, headers={"content-type": "text/html"}, text=text)
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.browse(text_filter="Moqueuse", cursor="1", limit=1)
    assert len(result["items"]) == 1
    assert result["collection_request_count"] == 2
    assert requests == ["/livres/", "/livres/63047-moqueuse/"]
    assert source._call_requests.get() is None

    def fail_detail(req):
        if req.url.path == "/livres/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=LISTING)
        return httpx.Response(503, headers={"content-type": "text/html"}, text="private")
    source.client = httpx.Client(transport=httpx.MockTransport(fail_detail))
    with pytest.raises(RuntimeError, match="503") as exc:
        source.browse(text_filter="Moqueuse", cursor="1", limit=1)
    assert exc.value.collection_request_count == 2
    assert source._call_requests.get() is None


def test_milan_cooperative_cancel_after_listing_prevents_all_detail_http():
    from milan_youth import MilanCollectionCancelled
    state, requests = {"cancel": False}, []

    def handler(request):
        requests.append(request.url.path)
        state["cancel"] = True
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text=LISTING,
        )

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(MilanCollectionCancelled) as err:
        source.browse_with_cancellation(
            cancelled=lambda: state["cancel"], age=8, limit=8,
        )
    assert requests == ["/livres/"]
    assert err.value.collection_request_count == 1
    assert source.diagnostic_snapshot()["state"] == "cancelled"
    assert source.diagnostic_snapshot()["phase"] == "listing"
    assert source._call_requests.get() is None


def test_milan_cooperative_cancel_between_details_avoids_unneeded_http():
    from milan_youth import MilanCollectionCancelled
    from types import SimpleNamespace

    state, requests, sent = {"cancel": False}, [], []
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda request: handler(request),
    )))

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/livres/":
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text=LISTING,
            )
        state["cancel"] = True
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text=MOQUEUSE,
        )

    source.activate(SimpleNamespace(
        report_diagnostic=lambda *args, **kwargs: sent.append((args, kwargs)),
    ))
    with pytest.raises(MilanCollectionCancelled) as err:
        source.browse_with_cancellation(
            cancelled=lambda: state["cancel"], age=8, limit=8,
        )
    assert requests == ["/livres/", "/livres/63047-moqueuse/"]
    assert err.value.collection_request_count == 2
    assert source.diagnostic_snapshot()["state"] == "cancelled"
    assert source.diagnostic_snapshot()["detail_requests"] == 1
    assert sent[-1][0] == ("Milan catalogue collection", "error")
    assert source._call_requests.get() is None

    # Next invocation is independent: no leaked cancellation flag or count.
    state["cancel"] = False
    source.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"},
            text=LISTING if request.url.path in {"/livres/", "/recherche/books/"} else MOQUEUSE,
        )
    ))
    success = source.browse_with_cancellation(
        cancelled=lambda: state["cancel"], text_filter="Moqueuse", cursor="1", limit=1,
    )
    assert success["collection_request_count"] == 2
    assert len(success["items"]) == 1
    assert source._call_requests.get() is None


def test_milan_already_cancelled_before_first_http_proves_zero_requests():
    from milan_youth import MilanCollectionCancelled

    requests = []
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(
            200, headers={"content-type": "text/html"}, text=LISTING,
        )
    )))
    with pytest.raises(MilanCollectionCancelled) as err:
        source.browse_with_cancellation(cancelled=lambda: True)
    assert requests == []
    assert err.value.collection_request_count == 0
    assert source.diagnostic_snapshot()["state"] == "cancelled"


def test_milan_search_and_legacy_browse_contracts_unchanged_by_extension():
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"},
            text=LISTING if request.url.path in {"/livres/", "/recherche/books/"} else MOQUEUSE,
        )
    )))
    assert source.search("Moqueuse", limit=1)["collection_request_count"] == 2
    assert source.browse(text_filter="Moqueuse", cursor="1", limit=1)["collection_request_count"] == 2


def test_milan_funnel_counts_listing_details_and_local_exclusions():
    requests = []
    def handler(req):
        requests.append(req.url.path)
        return httpx.Response(
            200, headers={"content-type": "text/html"},
            text=LISTING if req.url.path == "/livres/" else MOQUEUSE,
        )

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.browse(text_filter="Moqueuse", cursor="1", limit=1)
    funnel = result["collection_pipeline"]
    assert len(result["items"]) == 1
    assert requests == ["/livres/", "/livres/63047-moqueuse/"]
    assert result["collection_request_count"] == 2
    assert funnel["query_mode"] == "local"
    assert funnel["effective_limit"] == 1
    assert funnel["http_status"] == 200
    assert funnel["candidate_count"] == 3
    assert funnel["product_link_count"] == 3
    assert funnel["analyzed_count"] == 1
    assert funnel["isbn_valid_count"] == 1
    assert funnel["age_qualified_count"] == 1
    assert funnel["returned_items"] == 1
    assert funnel["rejection_reasons"] == {"text_filter_excluded": 2}
    assert funnel["http_ms"] >= 0 and funnel["parse_ms"] >= 0
    assert funnel["total_ms"] >= funnel["http_ms"]
    for forbidden in ("Moqueuse", "9782408020804", "/livres/"):
        assert forbidden not in repr(funnel)


def test_milan_funnel_distinguishes_missing_age_and_local_age_filter():
    requests = []
    def handler(req):
        requests.append(req.url.path)
        if req.url.path == "/livres/":
            html = LISTING
        elif req.url.path.endswith("adulte/"):
            html = ADULT
        elif req.url.path.endswith("lescargot/"):
            html = ESCARGOT
        else:
            html = MOQUEUSE
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = source.browse(age=0, limit=8)
    funnel = result["collection_pipeline"]
    assert result["items"] == ()
    assert result["collection_request_count"] == len(requests) == 4
    assert funnel["candidate_count"] == 3
    assert funnel["analyzed_count"] == 3
    assert funnel["isbn_valid_count"] == 2
    assert funnel["returned_items"] == 0
    assert funnel["rejection_reasons"] == {
        "age_unattested": 1, "age_filter_excluded": 2,
    }


def test_milan_cancelled_after_listing_preserves_per_call_funnel_and_zero_details():
    from milan_youth import MilanCollectionCancelled
    cancelled = {"value": False}
    source = MilanYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: (
            cancelled.__setitem__("value", True)
            or httpx.Response(200, headers={"content-type": "text/html"}, text=LISTING)
        )
    )))
    with pytest.raises(MilanCollectionCancelled) as exc:
        source.browse_with_cancellation(cancelled=lambda: cancelled["value"])
    assert exc.value.collection_request_count == 1
    funnel = exc.value.collection_pipeline
    assert funnel["query_mode"] == "local"
    assert funnel["effective_limit"] == 8
    assert funnel["http_status"] == 200
    assert funnel["http_ms"] >= 0
    assert "analyzed_count" not in funnel
