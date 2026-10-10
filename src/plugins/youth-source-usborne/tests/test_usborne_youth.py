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
    assert parse_listing(HTML, age=1) == ()


def test_invalid_or_external_product_links_are_ignored():
    html = """
      <a href="https://evil.example/book-9781805075103">External</a><p>Age: 3+</p>
      <a href="/us/not-a-product">No ISBN</a><p>Age: 3+</p>
      <a href="/us/bad-9780000000000">Bad ISBN</a><p>Age: 3+</p>
    """
    assert parse_listing(html) == ()


def test_next_cursor_requires_explicit_immediate_next_page_link():
    assert next_page_cursor(HTML, current_page=1) == "2"
    assert next_page_cursor("<a href='?p=2'>Next Page</a>", current_page=1) == "2"
    assert next_page_cursor("<a href='?p=3'>Next Page</a>", current_page=1) == ""
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
    with pytest.raises(ValueError):
        source.browse(cursor="0")
    with pytest.raises(ValueError):
        source.browse(cursor="101")
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



def test_duplicate_badge_after_title_does_not_lose_published_age():
    html = """
      <a href="/us/book-9781805075103">Real Product Title</a>
      <div>Age: 3+</div>
      <a href="/us/book-9781805075103">Award Winner</a>
      <a href="/us/older-9780306406157">Older</a>
      <div>Age: 10+</div>
    """
    rows = parse_listing(html)
    assert [row["title"] for row in rows] == ["Real Product Title", "Older"]
    assert rows[0]["age_ranges"][0]["age_min"] == 3


def test_redirect_response_is_rejected_not_followed():
    source = UsborneYouthSource(
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
        source.browse()


def test_browse_path_prefix_spoof_is_rejected():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)

    source = UsborneYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError):
        source._get("https://usborne.com/us/books/browse-by-age-evil")
    assert calls == 0


def test_local_filter_is_bounded_to_300_characters(monkeypatch):
    captured = []

    def fake_parse(html, *, text_filter="", age=None, limit=40):
        captured.append(text_filter)
        return ()

    monkeypatch.setattr("usborne_youth.parse_listing", fake_parse)
    source = UsborneYouthSource(
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=HTML,
            )
        ))
    )
    source.search("older" + ("x" * 500))
    assert len(captured) == 1
    assert len(captured[0]) == 300
    assert captured[0].startswith("older")


def test_usborne_diagnostic_success_and_local_snapshot_without_extra_network():
    import json
    from types import SimpleNamespace

    sent, requests = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)

    source = UsborneYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.diagnostic_snapshot() == {}
    assert source.activate(SimpleNamespace(
        report_diagnostic=lambda *args, **kwargs: sent.append((args, kwargs))
    )) is source
    output = source.browse(age=3, limit=3)
    assert len(output["items"]) == 2
    d = source.diagnostic_snapshot()
    assert d["source"] == "usborne" and d["phase"] == "complete"
    assert d["state"] == "items" and d["request_count"] == 1
    assert d["http_status"] == 200 and d["content_type_is_html"] is True
    assert d["html_bytes"] == len(HTML.encode("utf-8"))
    assert len(d["html_sha256_prefix"]) == 16
    assert d["product_url_count"] == 3 and d["anchor_count"] == 5
    assert d["returned_items"] == 2 and d["qualified_age_count"] == 2
    assert d["next_cursor_present"] is True
    assert d["age_filter_present"] is True
    assert d["duration_ms"] >= 0 and "T" in d["attempted_at_utc"]
    assert sent[-1][0] == ("Usborne youth catalogue collection", "ok")
    assert sent[-1][1]["detail"] == d
    assert sent[-1][1]["level"] == "normal"
    assert len(requests) == 1
    encoded = json.dumps(d)
    assert "Why School" not in encoded
    assert "/us/" not in encoded
    assert "9781805075103" not in encoded


def test_usborne_diagnostic_error_and_timeout_are_typed_not_logged_raw():
    source = UsborneYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(429, headers={"content-type": "text/html"}, text="secret token")
    )))
    with pytest.raises(RuntimeError, match="429"):
        source.browse(age=3)
    d = source.diagnostic_snapshot()
    assert (d["http_status"], d["request_count"], d["state"]) == (429, 1, "error")
    assert d["phase"] == "listing" and d["error_type"] == "RuntimeError"
    assert "secret token" not in str(d)

    def timeout(req):
        raise httpx.ReadTimeout("private URL", request=req)

    source.client = httpx.Client(transport=httpx.MockTransport(timeout))
    with pytest.raises(httpx.ReadTimeout):
        source.browse(age=3)
    d = source.diagnostic_snapshot()
    assert d["request_count"] == 1
    assert d["error_type"] == "ReadTimeout"
    assert "http_status" not in d and "private URL" not in str(d)


def test_usborne_diagnostic_empty_does_not_mark_site_healthy():
    source = UsborneYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>no books</p>"
        )
    )))
    assert source.browse(age=5)["items"] == ()
    d = source.diagnostic_snapshot()
    assert d["state"] == "empty_unverified" and d["returned_items"] == 0
    assert d["product_url_count"] == 0


def test_usborne_diagnostic_broker_failure_does_not_change_collection():
    from types import SimpleNamespace

    source = UsborneYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)
    )))
    source.activate(SimpleNamespace(
        report_diagnostic=lambda *args, **kwargs: 1 / 0
    ))
    assert len(source.browse(age=3)["items"]) == 2
    assert source.diagnostic_snapshot()["state"] == "items"


def test_http_error_preserves_typed_status_for_youth_diagnostics():
    """A single mocked HTTP request; no remote publisher contacted."""
    source = UsborneYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            503, headers={"content-type": "text/html"}, text="private body",
        )
    )))
    with pytest.raises(RuntimeError, match="HTTP 503") as raised:
        source.browse(age=8)
    assert raised.value.http_status == 503




def test_usborne_per_collection_request_evidence_is_exact_on_success_and_error():
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)
    source = UsborneYouthSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.browse(age=3)["collection_request_count"] == 1
    assert len(requests) == 1
    source.client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(429, headers={"content-type": "text/html"}, text="private")
    ))
    with pytest.raises(RuntimeError, match="429") as exc:
        source.browse(age=3)
    assert exc.value.collection_request_count == 1
    assert source._call_requests.get() is None


def test_paginated_usborne_layout_without_book_links_is_invalid_not_empty():
    from usborne_youth import InvalidCatalogueStructureError
    from types import SimpleNamespace

    sent, requests = [], []
    source = UsborneYouthSource(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: requests.append(req) or httpx.Response(
            200, headers={"content-type": "text/html"},
            text='<main><p>Layout changed</p><a href="?p=2">Next Page</a></main>',
        )
    )))
    source.activate(SimpleNamespace(report_diagnostic=lambda *a, **kw: sent.append((a, kw))))
    with pytest.raises(InvalidCatalogueStructureError, match="no recognizable product links") as raised:
        source.browse(age=3)
    assert raised.value.collection_request_count == 1
    assert len(requests) == 1
    snapshot = source.diagnostic_snapshot()
    assert snapshot["phase"] == "parsing"
    assert snapshot["state"] == "invalid"
    assert snapshot["error_type"] == "InvalidCatalogueStructureError"
    assert "Layout changed" not in repr(snapshot)
    assert sent[-1][0][-1] == "error"

    # A legitimate local filter still can produce a zero-row page.
    source.client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)
    ))
    page = source.browse(text_filter="missing query")
    assert page["items"] == ()
    assert page["next_cursor"] == "2"
    assert source.diagnostic_snapshot()["state"] == "empty_unverified"
