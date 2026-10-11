from __future__ import annotations

from literary_criticism import (
    CriticismIndex,
    LiteraryCriticismPlugin,
    discover_feed_links,
    discover_feeds,
    match_article,
    normalize_article,
    validate_source_contract,
)


class GoodSource:
    def source_info(self):
        return {"source_id": "revue.test", "display_name": "Revue test", "homepage_url": "https://revue.test/"}

    def discover(self, query, limit=20):
        return []

    def collect(self, cursor=None, limit=50):
        return {
            "items": [
                {
                    "id": "a1",
                    "title": "Une lecture",
                    "url": "https://revue.test/article/1#fragment",
                    "author": "Critique",
                    "summary": "Extrait court",
                    "book": {
                        "title": "Le Livre",
                        "authors": ["Autrice"],
                        "isbn13": "9780306406157",
                        "notice_url": "https://catalogue.test/book/42",
                    },
                }
            ],
            "next_cursor": "next",
            "fresh_for_seconds": 60,
        }


class BrokenSource(GoodSource):
    def collect(self, cursor=None, limit=50):
        raise RuntimeError("network down")


def test_source_contract_is_structural():
    info = validate_source_contract(GoodSource())
    assert info.source_id == "revue.test"
    assert info.display_name == "Revue test"


def test_source_contract_rejects_missing_collect():
    class Incomplete:
        def source_info(self):
            return {"source_id": "bad.test", "display_name": "Bad"}

        def discover(self, query, limit=20):
            return []

    try:
        validate_source_contract(Incomplete())
    except TypeError as exc:
        assert "collect" in str(exc)
    else:
        raise AssertionError("missing collect() must be rejected")


def test_html_feed_discovery_supports_relative_rss_and_atom():
    html = """
    <html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml" title="RSS">
      <link rel="alternate" type="application/atom+xml; charset=utf-8" href="https://example.test/atom" title="Atom">
      <link rel="stylesheet" href="/style.css">
    </head></html>
    """
    found = discover_feed_links("https://example.test/articles/1", html)
    assert [item.url for item in found] == [
        "https://example.test/feed.xml",
        "https://example.test/atom",
    ]
    assert [item.format for item in found] == ["rss", "atom"]


def test_metasearch_discovery_is_preview_only_and_deduplicated():
    calls = []

    def search(query, limit):
        calls.append((query, limit))
        return [
            {
                "url": "https://media.test/reviews",
                "title": "Media",
                "feed_url": "/rss",
                "html": '<link rel="alternate" type="application/rss+xml" href="/rss">',
            },
            {
                "url": "https://other.test/",
                "feed_urls": ["https://other.test/atom"],
            },
        ]

    found = discover_feeds("  littérature  ", search, limit=10)
    assert calls == [("littérature", 10)]
    assert [item.url for item in found] == [
        "https://media.test/rss",
        "https://other.test/atom",
    ]


def test_collection_isolates_source_failure():
    plugin = LiteraryCriticismPlugin()
    report = plugin.collect_source(BrokenSource())
    assert report.source_id == "revue.test"
    assert report.accepted == 0
    assert report.errors == ("collect: source_error",)
    assert plugin.index.articles() == ()


def test_collection_normalizes_deduplicates_and_tracks_freshness():
    plugin = LiteraryCriticismPlugin()
    first = plugin.collect_source(GoodSource())
    second = plugin.collect_source(GoodSource())

    assert first.accepted == 1
    assert first.next_cursor == "next"
    assert first.fresh_until
    assert second.accepted == 0
    assert second.duplicates == 1
    article = plugin.index.articles()[0]
    assert article.url == "https://revue.test/article/1"
    assert article.isbns == ("9780306406157",)
    assert article.notice_urls == ("https://catalogue.test/book/42",)
    assert article.excerpt == "Extrait court"


def test_invalid_items_do_not_abort_valid_items():
    index = CriticismIndex()
    report = index.ingest(
        "source.test",
        [
            {"title": "Sans URL"},
            {"title": "Valide", "url": "https://source.test/ok"},
        ],
    )
    assert report.accepted == 1
    assert len(report.errors) == 1
    assert len(index.articles()) == 1


def test_matching_prefers_isbn_then_notice_then_title_author():
    by_isbn = normalize_article(
        {
            "title": "Critique",
            "url": "https://reviews.test/1",
            "book": {"title": "Wrong", "isbn13": "9780306406157"},
        },
        source_id="reviews",
    )
    match = match_article(by_isbn, {"title": "Other", "isbn": "978-0-306-40615-7"})
    assert (match.status, match.score) == ("confirmed", 100)
    assert match.reasons == ("isbn:9780306406157",)

    by_notice = normalize_article(
        {
            "title": "Critique 2",
            "url": "https://reviews.test/2",
            "book": {"notice_url": "https://catalogue.test/book/1"},
        },
        source_id="reviews",
    )
    match = match_article(by_notice, {"friendly_url": "https://catalogue.test/book/1#top"})
    assert (match.status, match.score, match.reasons) == ("confirmed", 90, ("notice-url",))

    by_title = normalize_article(
        {
            "title": "Critique 3",
            "url": "https://reviews.test/3",
            "book": {"title": "Le Livre", "authors": ["Autrice"]},
        },
        source_id="reviews",
    )
    match = match_article(by_title, {"title": " le   livre ", "authors": ["AUTRICE"]})
    assert (match.status, match.score) == ("confirmed", 75)
    assert match.reasons == ("title-exact", "author-exact")


def test_weak_title_only_match_stays_suggestion():
    article = normalize_article(
        {
            "title": "Critique",
            "url": "https://reviews.test/weak",
            "book": {"title": "Le Livre"},
        },
        source_id="reviews",
    )
    match = match_article(article, {"title": "Le Livre", "authors": ["Quelqu'un"]})
    assert (match.status, match.score) == ("suggested", 45)


def test_equal_strong_matches_are_marked_ambiguous():
    article = normalize_article(
        {
            "title": "Critique",
            "url": "https://reviews.test/ambiguous",
            "book": {"isbn13": "9780306406157"},
        },
        source_id="reviews",
    )
    index = CriticismIndex()
    ranked = index.ranked_books(
        article,
        [
            {"book_id": "edition-a", "isbn13": "9780306406157"},
            {"book_id": "edition-b", "isbn13": "9780306406157"},
        ],
    )
    assert len(ranked) == 2
    assert all(match.status == "suggested" for _, match in ranked)
    assert all("ambiguous" in match.reasons for _, match in ranked)


def test_cross_source_duplicate_keeps_provenance():
    index = CriticismIndex()
    index.ingest("source.a", [{"title": "Critique", "url": "https://reviews.test/same"}])
    report = index.ingest("source.b", [{"title": "Critique", "url": "https://reviews.test/same#x"}])
    assert report.duplicates == 1
    assert index.articles()[0].provenance == ("source.a", "source.b")

