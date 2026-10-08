import httpx
import pytest

from booktrust_youth import (
    BOOKFINDER,
    BookTrustSource,
    age_group_for,
    parse_detail,
    parse_recommendations,
)

LIST_HTML = """
<html><body>
<article>
  <h3><a href="/book-recommendations/bookfinder/press-here/">Press Here</a></h3>
  <p>by Hervé Tullet 2011</p>
  <p>For ages 2 to 5</p>
  <ul><li>Interactive</li><li>Picture books</li></ul>
</article>
<article>
  <h3><a href="/book-recommendations/bookfinder/quest/">Quest</a></h3>
  <p>edited by Daniel Hahn 2017</p>
  <p>For ages 8 to 12</p>
  <ul><li>Adventure</li><li>Historical</li></ul>
</article>
<a href="/book-recommendations/bookfinder/">Explore our Bookfinder</a>
</body></html>
"""

DETAIL_HTML = """
<html><body>
<nav><a href="/">Home</a></nav>
<h1>Press Here</h1>
<p>by Hervé Tullet</p>
<p>Interest age: 2+</p>
<p>Reading age: 5+</p>
<p>Published by Chronicle Books, 2011</p>
<ul><li>Interactive</li></ul>
<h2>About this book</h2>
<p>A description that should not become a theme because it is deliberately much longer than the theme labels.</p>
</body></html>
"""


def test_age_group_maps_to_narrow_attested_bucket():
    assert age_group_for(0) == "0 to 1 year"
    assert age_group_for(6) == "5 to 7 years"
    assert age_group_for(10) == "9 to 11 years"
    assert age_group_for(15) == ""
    with pytest.raises(ValueError):
        age_group_for(18)


def test_list_parser_extracts_book_links_age_and_themes():
    cards = parse_recommendations(LIST_HTML)
    assert [c.title for c in cards] == ["Press Here", "Quest"]
    assert cards[0].author == "Hervé Tullet"
    assert cards[0].year == 2011
    assert cards[0].recommended_age == "2 to 5"
    assert "Interactive" in cards[0].themes


def test_list_parser_local_filter_does_not_crawl():
    cards = parse_recommendations(LIST_HTML, text_filter="historical")
    assert [c.title for c in cards] == ["Quest"]


def test_list_parser_rejects_external_links():
    html = '<h3><a href="https://evil.example/book-recommendations/bookfinder/x/">Bad</a></h3>'
    assert parse_recommendations(html) == ()


def test_detail_parser_preserves_interest_and_reading_age():
    detail = parse_detail(DETAIL_HTML, url=BOOKFINDER + "press-here/")
    assert detail["interest_age"] == "2+"
    assert detail["reading_age"] == "5+"
    assert detail["publisher"] == "Chronicle Books"
    assert detail["published_year"] == 2011
    assert detail["metadata"]["age_evidence"] == "booktrust.detail.explicit"


def test_source_info_is_contract_compatible():
    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    assert source.source_info()["source_id"] == "booktrust"


def test_browse_uses_attested_age_group_and_returns_next_cursor():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=LIST_HTML)

    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    page = source.browse(age=10, themes=["Adventure"], cursor="2", limit=1)
    assert len(page["items"]) == 1
    assert page["next_cursor"] == "3"
    assert "ageGroups=9+to+11+years" in seen[0]
    assert "bookfinderCategories%5B%5D=Adventure" in seen[0]
    assert "page=2" in seen[0]


def test_search_filters_only_current_page():
    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text=LIST_HTML)
    )))
    page = source.search("quest")
    assert [item["title"] for item in page["items"]] == ["Quest"]


def test_detail_rejects_non_booktrust_url_before_network():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=DETAIL_HTML)

    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError):
        source.detail("https://example.org/book")
    assert calls == 0


def test_http_failure_is_explicit():
    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(503, headers={"content-type": "text/html"}, text="nope")
    )))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        source.browse()


def test_non_html_and_oversized_response_are_rejected():
    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "application/json"}, text="{}")
    )))
    with pytest.raises(RuntimeError, match="not HTML"):
        source.browse()

    source2 = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 2_000_001)
    )))
    with pytest.raises(RuntimeError, match="2 MB"):
        source2.browse()
