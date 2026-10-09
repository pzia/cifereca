import httpx
import pytest

from booktrust_youth import (
    BOOKFINDER,
    BookTrustSource,
    age_group_for,
    parse_detail,
    parse_recommendations,
    next_cursor,
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
<a href="/book-recommendations/bookfinder/bookfinder-recommendations/?page=3">Next</a>
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



def test_next_cursor_requires_explicit_next_page_link():
    assert next_cursor(LIST_HTML, current_page=2) == "3"
    assert next_cursor('<a href="?page=3">Next</a>', current_page=2) == "3"
    assert next_cursor('<a href="?page=5">Next</a>', current_page=2) == ""
    assert next_cursor("<p>End</p>", current_page=2) == ""


def test_unsupported_age_returns_empty_without_unfiltered_network_call():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, text=LIST_HTML)

    source = BookTrustSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    page = source.browse(age=15)
    assert page == {"items": (), "next_cursor": "", "fresh_for_seconds": 86400}
    assert calls == 0


def test_invalid_cursor_is_rejected_before_network():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, text=LIST_HTML)

    source = BookTrustSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError):
        source.browse(cursor="bad")
    with pytest.raises(ValueError):
        source.browse(cursor="101")
    assert calls == 0


def test_credentials_nonstandard_port_and_redirect_are_rejected():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"content-type": "text/html", "location": "https://evil.example/"},
            text="redirect",
        )

    source = BookTrustSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError):
        source.detail("https://user:secret@www.booktrust.org.uk/book-recommendations/bookfinder/x/")
    with pytest.raises(ValueError):
        source.detail("https://www.booktrust.org.uk:444/book-recommendations/bookfinder/x/")
    assert calls == 0

    with pytest.raises(RuntimeError, match="HTTP 302"):
        source.browse()
    assert calls == 1


def test_script_and_style_text_do_not_become_age_or_theme_evidence():
    html = """
      <script>For ages 1 to 2</script>
      <style>.x:after { content: 'For ages 3 to 4'; }</style>
      <h3><a href="/book-recommendations/bookfinder/clean/">Clean Book</a></h3>
      <p>by Clean Author 2020</p>
      <p>For ages 8 to 10</p>
      <p>Adventure</p>
    """
    cards = parse_recommendations(html)
    assert len(cards) == 1
    assert cards[0].recommended_age == "8 to 10"
    assert "Adventure" in cards[0].themes


def test_booktrust_http_outage_preserves_catalog_cache_then_recovers():
    """Real BookTrust adapter + real youth cache, no external HTTP requests."""
    from pathlib import Path
    import sys
    catalog_dir = Path(__file__).resolve().parents[2] / "youth-catalog"
    if str(catalog_dir) not in sys.path:
        sys.path.insert(0, str(catalog_dir))
    from youth_catalog import YouthCatalogPlugin

    state = {"response": "ok", "calls": 0}

    def handler(request):
        state["calls"] += 1
        if state["response"] == "unavailable":
            return httpx.Response(503, headers={"content-type": "text/html"}, text="unavailable")
        if state["response"] == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        html = LIST_HTML.replace("Quest", "Recovered Quest") if state["response"] == "recovered" else LIST_HTML
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    source = BookTrustSource(client=client)
    catalog = YouthCatalogPlugin()
    first = catalog.collect_source("camille", source, "")
    assert first.accepted == 2 and first.rejected == 0
    assert [book.title for book in catalog.search_cached("camille", age=8)] == ["Quest"]
    assert catalog.search_cached("alex") == ()
    fresh_before = catalog.index.fresh_until("camille", "booktrust")
    assert fresh_before
    calls_before = state["calls"]
    catalog.search_cached("camille", age=8)
    assert state["calls"] == calls_before  # reading the cache is offline

    for outage in ("unavailable", "timeout"):
        state["response"] = outage
        report = catalog.collect_source("camille", source, "")
        assert report.rejected == 1 and report.accepted == 0
        assert "search:" in report.errors[0]
        assert [book.title for book in catalog.search_cached("camille", age=8)] == ["Quest"]
        assert catalog.index.fresh_until("camille", "booktrust") == fresh_before
        assert catalog.search_cached("alex") == ()

    state["response"] = "recovered"
    recovered = catalog.collect_source("camille", source, "")
    assert recovered.accepted == 2 and recovered.rejected == 0
    assert [book.title for book in catalog.search_cached("camille", age=8)] == ["Recovered Quest"]
    assert catalog.search_cached("camille", "Quest")  # result is explicit, after recovery
    assert state["calls"] == calls_before + 3


def test_booktrust_explicit_browse_publishes_redacted_cache_only_diagnostic():
    import json
    from types import SimpleNamespace

    calls, published = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/html"},
            text=LIST_HTML,
        )

    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    context = SimpleNamespace(report_diagnostic=lambda *args, **kwargs: published.append((args, kwargs)))
    assert source.activate(context) is source
    assert source.diagnostic_snapshot() == {}
    result = source.browse(age=10, cursor="2", limit=1)
    assert len(result["items"]) == 1 and result["next_cursor"] == "3"
    assert len(calls) == 1

    diag = source.diagnostic_snapshot()
    assert diag["source"] == "booktrust"
    assert diag["phase"] == "complete"
    assert diag["state"] == "items"
    assert diag["request_count"] == 1
    assert diag["http_status"] == 200
    assert diag["content_type_is_html"] is True
    assert diag["html_bytes"] == len(LIST_HTML.encode("utf-8"))
    assert len(diag["html_sha256_prefix"]) == 16
    assert diag["anchor_count"] == 4
    assert diag["book_url_count"] == 2
    assert diag["book_named_link_count"] == 2
    assert diag["candidate_books_count"] == 2
    assert diag["returned_items"] == 1
    assert diag["cards_with_published_age"] == 1
    assert diag["next_cursor_present"] is True
    assert diag["age_filter_present"] is True
    assert "T" in diag["attempted_at_utc"]
    assert diag["duration_ms"] >= 0
    assert published[-1][0] == ("BookTrust Bookfinder collection", "ok")
    assert published[-1][1]["level"] == "normal"
    assert published[-1][1]["detail"] == diag
    payload = json.dumps(diag)
    for forbidden in ("Press Here", "Quest", "Hervé", "bookfinder/", "ageGroups=", "private"):
        assert forbidden not in payload
    assert len(calls) == 1  # diagnostics never fetch a page


def test_booktrust_browse_errors_preserve_http_and_typed_state_without_url():
    from types import SimpleNamespace

    published = []
    def callback(*args, **kwargs):
        published.append((args, kwargs))

    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(
            429, headers={"content-type": "text/html"},
            text="retry secret-token",
        )
    )))
    source.activate(SimpleNamespace(report_diagnostic=callback))
    with pytest.raises(RuntimeError, match="HTTP 429"):
        source.browse(age=8)
    diag = source.diagnostic_snapshot()
    assert diag["source"] == "booktrust"
    assert diag["request_count"] == 1
    assert diag["http_status"] == 429
    assert diag["phase"] == "listing"
    assert diag["state"] == "error"
    assert diag["error_type"] == "RuntimeError"
    assert "secret-token" not in str(diag)
    assert "bookfinder/" not in str(diag)
    assert published[-1][0] == ("BookTrust Bookfinder collection", "error")

    def timeout(req):
        raise httpx.ReadTimeout("sensitive path", request=req)

    source.client = httpx.Client(transport=httpx.MockTransport(timeout))
    with pytest.raises(httpx.ReadTimeout):
        source.browse(age=8)
    diag = source.diagnostic_snapshot()
    assert diag["error_type"] == "ReadTimeout"
    assert diag["request_count"] == 1
    assert "http_status" not in diag
    assert "sensitive path" not in str(diag)


def test_booktrust_unsupported_age_does_not_claim_remote_success_or_issue_request():
    calls, statuses = [], []
    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, text=LIST_HTML)
    )))
    from types import SimpleNamespace

    source.activate(SimpleNamespace(
        report_diagnostic=lambda message, status, **kwargs: statuses.append(status),
    ))
    page = source.browse(age=15)
    assert page["items"] == ()
    assert calls == []
    diag = source.diagnostic_snapshot()
    assert diag["request_count"] == 0
    assert diag["phase"] == "not_requested"
    assert diag["state"] == "unsupported_age_bucket"
    assert statuses[-1] == "skipped"


def test_booktrust_diagnostic_broker_errors_never_break_browse():
    from types import SimpleNamespace

    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text=LIST_HTML)
    )))
    source.activate(SimpleNamespace(
        report_diagnostic=lambda *args, **kwargs: 1 / 0,
    ))
    result = source.browse(age=8, limit=2)
    assert len(result["items"]) == 2
    assert source.diagnostic_snapshot()["state"] == "items"


def test_booktrust_empty_page_is_unverified_not_automatically_live_healthy():
    source = BookTrustSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text="<p>No books</p>")
    )))
    page = source.browse(age=8)
    assert page["items"] == []
    assert source.diagnostic_snapshot()["state"] == "empty_unverified"
    assert source.diagnostic_snapshot()["returned_items"] == 0
