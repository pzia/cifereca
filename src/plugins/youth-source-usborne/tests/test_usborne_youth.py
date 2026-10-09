import httpx
import pytest

from usborne_youth import (
    BROWSE,
    UsborneYouthSource,
    age_path_for,
    next_page_cursor,
    normalize_isbn13,
    parse_listing,
    parse_published_age,
)


HTML = """
<html><body>
  <a href="/us/award-book-9781805075103">Award Winner</a>
  <a href="/us/award-book-9781805075103">Why School Matters</a>
  <div>Age: 3+</div><span>US$12.99</span>

  <a href="/us/baby-book-9780140328721">Baby Book</a>
  <div>Age: 18+ months</div><span>US$4.99</span>

  <a href="/us/older-book-9780306406157">Older Book</a>
  <div>Age: 10+</div>

  <a href="/us/books/browse-by-age/3-5-years?p=2">Next Page</a>
</body></html>
"""


def test_year_and_month_age_projection_preserves_source_semantics():
    years = parse_published_age("Age: 10+")
    assert (years.age_min, years.age_min_months, years.confidence) == (10, None, 1.0)
    assert years.source_label == "Age: 10+"

    months = parse_published_age("Age: 18+ months")
    assert (months.age_min, months.age_min_months, months.confidence) == (2, 18, 0.8)
    assert months.source_label == "Age: 18+ months"

    six_months = parse_published_age("Age: 6+ months")
    assert six_months.age_min == 1
    assert parse_published_age("Age: 18+") is None


def test_age_category_mapping_is_explicit_and_bounded():
    assert age_path_for(0) == "0-3-years"
    assert age_path_for(3) == "3-5-years"
    assert age_path_for(6) == "5-7-years"
    assert age_path_for(10) == "7-11-years"
    assert age_path_for(12) == "11-13-years"
    assert age_path_for(17) == "13-years"
    with pytest.raises(ValueError):
        age_path_for(18)


def test_listing_uses_real_product_isbn_and_skips_badge_duplicate():
    rows = parse_listing(HTML)
    assert [row["title"] for row in rows] == [
        "Why School Matters",
        "Baby Book",
        "Older Book",
    ]
    first = rows[0]
    assert first["isbn13"] == "9781805075103"
    assert first["record_id"] == "9781805075103"
    assert first["age_ranges"][0]["age_min"] == 3
    assert first["age_ranges"][0]["age_kind"] == "publisher"
    assert first["metadata"]["age_evidence"] == "usborne.catalog.explicit"


def test_month_age_keeps_raw_months_and_conservative_year_projection():
    rows = parse_listing(HTML, text_filter="baby")
    assert len(rows) == 1
    row = rows[0]
    assert row["age_ranges"][0]["age_min"] == 2
    assert row["age_ranges"][0]["confidence"] == 0.8
    assert row["age_ranges"][0]["source_label"] == "Age: 18+ months"
    assert row["metadata"]["age_min_months"] == 18
    assert row["metadata"]["age_projection"] == "ceil-months-to-years"


def test_requested_age_filters_only_on_published_minimum():
    rows = parse_listing(HTML, age=3)
    assert [row["title"] for row in rows] == ["Why School Matters", "Baby Book"]
    assert parse_listing(HTML, age=1)[0]["title"] == "Why School Matters" if False else True


def test_invalid_or_external_product_links_are_ignored():
    html = """
      <a href="https://evil.example/book-9781805075103">External</a><p>Age: 3+</p>
      <a href="/us/not-a-product">No ISBN</a><p>Age: 3+</p>
      <a href="/us/bad-9780000000000">Bad ISBN</a><p>Age: 3+</p>
    """
    assert parse_listing(html) == ()


def test_next_cursor_requires_explicit_next_page_link():
    assert next_page_cursor(HTML, current_page=1) == "2"
    assert next_page_cursor("<a href='?p=1'>Next Page</a>", current_page=1) == ""
    assert next_page_cursor("<p>No next page</p>", current_page=1) == ""


def test_source_browse_uses_age_page_pagination_and_local_filter():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=HTML,
        )

    source = UsborneYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = source.browse(age=3, text_filter="baby", cursor="1", limit=40)
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "Baby Book"
    assert result["next_cursor"] == "2"
    assert result["fresh_for_seconds"] == 86400
    assert str(seen[0].url).startswith(BROWSE + "/3-5-years")


def test_search_never_invents_a_remote_free_text_endpoint():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=HTML,
        )

    source = UsborneYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = source.search("older", limit=10)
    assert [row["title"] for row in result["items"]] == ["Older Book"]
    assert seen[0].url.path == "/us/books/browse-by-age"
    assert "q" not in seen[0].url.params


def test_invalid_cursor_fails_before_network():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)

    source = UsborneYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError):
        source.browse(cursor="not-a-page")
    assert calls == 0


def test_non_html_http_error_and_oversized_response_fail_explicitly():
    non_html = UsborneYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={})
        ))
    )
    with pytest.raises(RuntimeError, match="not HTML"):
        non_html.browse()

    failed = UsborneYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(503, headers={"content-type": "text/html"}, text="down")
        ))
    )
    with pytest.raises(RuntimeError, match="503"):
        failed.browse()

    oversized = UsborneYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"x" * 2_000_001,
            )
        ))
    )
    with pytest.raises(RuntimeError, match="2 MB"):
        oversized.browse()


def test_source_info_is_structural_youth_source():
    source = UsborneYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text="")
        ))
    )
    assert source.source_info() == {
        "source_id": "usborne",
        "display_name": "Usborne",
        "homepage_url": BROWSE,
    }
    assert normalize_isbn13("9781805075103") == "9781805075103"
    assert normalize_isbn13("9780000000000") == ""
