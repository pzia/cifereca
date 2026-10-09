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
    result = source.search("moqueuse", limit=8)
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
