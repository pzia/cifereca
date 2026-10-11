"""P2 #709: metasearch and HTML feed discovery are bounded previews.

All inputs synthetic. No HTTP, sockets, GTK or external services are used.
"""
from __future__ import annotations

from itertools import count

import pytest

from literary_criticism import (
    MAX_DISCOVERY_HTML_CHARS,
    MAX_FEED_LINKS_PER_PAGE,
    MAX_FEED_URLS_PER_ROW,
    MAX_SEARCH_ROWS_PER_RESULT,
    discover_feed_links,
    discover_feeds,
)


def test_infinite_metasearch_without_feeds_returns_after_hard_row_budget():
    observed = []
    calls = []

    def search(query, limit):
        calls.append((query, limit))
        def endless():
            for number in count():
                observed.append(number)
                yield {"url": f"https://media.example/post/{number}", "title": "post"}
        return endless()

    assert discover_feeds("littérature", search, limit=3) == ()
    assert calls == [("littérature", 3)]
    assert len(observed) == 3 * MAX_SEARCH_ROWS_PER_RESULT


def test_infinite_feed_urls_per_row_is_read_at_most_eight_items():
    observations = []

    def urls():
        for number in count():
            observations.append(number)
            yield f"javascript:privateToken{number}"

    search = lambda *_: iter([{
        "url": "https://reviews.example/article",
        "feed_urls": urls(),
    }])
    assert discover_feeds("critique", search, limit=12) == ()
    assert len(observations) == MAX_FEED_URLS_PER_ROW


def test_oversized_html_is_not_parsed_even_if_it_contains_links():
    valid = '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
    too_large = " " * MAX_DISCOVERY_HTML_CHARS + valid
    assert discover_feed_links("https://reviews.example/page", too_large) == ()
    assert discover_feed_links("https://reviews.example/page", None) == ()
    assert discover_feed_links("https://reviews.example/page", b"<link>") == ()


def test_html_parser_only_keeps_first_thirty_two_alternate_links():
    html = "".join(
        f'<link rel="alternate" type="application/rss+xml" href="/feed/{n}.xml">'
        for n in range(MAX_FEED_LINKS_PER_PAGE + 60)
    )
    found = discover_feed_links("https://reviews.example/page", html)
    assert len(found) == MAX_FEED_LINKS_PER_PAGE
    assert found[0].url == "https://reviews.example/feed/0.xml"
    assert found[-1].url == f"https://reviews.example/feed/{MAX_FEED_LINKS_PER_PAGE - 1}.xml"
    assert all(feed.evidence == "html-alternate" for feed in found)


def test_first_valid_feed_found_after_empty_pages_and_deduplicated():
    rows = [
        {"url": "https://reviews.example/article/0", "feed_urls": []},
        {"url": "https://reviews.example/article/1", "feed_url": ""},
        {"url": "https://reviews.example/article/2", "feed_url": "../feed.xml"},
        {"url": "https://reviews.example/article/3",
         "html": '<link rel="alternate" type="application/rss+xml" href="../feed.xml">'},
    ]
    found = discover_feeds("roman", lambda *args: (row for row in rows), limit=2)
    assert [feed.url for feed in found] == ["https://reviews.example/feed.xml"]


def test_early_limit_does_not_exhaust_metasearch_stream():
    observed = []
    def rows():
        for n in count():
            observed.append(n)
            yield {"url": "https://reviews.example/page", "feed_url": f"/rss/{n}"}
    found = discover_feeds("livres", lambda *_: rows(), limit=3)
    assert len(found) == 3
    assert len(observed) == 3


def test_page_with_many_feed_candidates_never_exceeds_requested_result_count():
    html = "".join(
        f'<link rel="alternate" type="application/atom+xml" href="/atom/{n}">'
        for n in range(80)
    )
    found = discover_feeds(
        "livres",
        lambda *_: [{"url": "https://reviews.example/index", "html": html}],
        limit=5,
    )
    assert len(found) == 5
    assert all(x.format == "atom" and x.evidence == "html-alternate" for x in found)


def test_html_link_discovery_does_not_activate_source_or_query_network():
    calls = []
    result = discover_feeds(
        "lit",
        lambda query, limit: calls.append((query, limit)) or [{
            "url": "https://reviews.example/",
            "html": '<link type="application/feed+json" rel="alternate" href="/feed.json">',
        }],
        limit=1,
    )
    assert calls == [("lit", 1)]
    assert result[0].format == "jsonfeed"
    assert result[0].url == "https://reviews.example/feed.json"


def test_broken_noniterable_metasearch_is_not_treated_as_network_fallback():
    assert discover_feeds("livres", lambda *_: 123, limit=2) == ()
    assert discover_feeds("", lambda *_: (_ for _ in ()), limit=2) == ()
