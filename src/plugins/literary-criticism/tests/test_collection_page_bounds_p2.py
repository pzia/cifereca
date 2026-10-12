"""P2 #709: bounded criticism ingestion, preserving last-good cache."""
from __future__ import annotations

from literary_criticism import LiteraryCriticismPlugin, normalize_article


def article(n):
    return {
        "url": f"https://book-reviews.example/review/{n}",
        "title": f"Critique {n}",
        "book_title": "Livre",
        "book_author": "Auteur",
    }


class Feed:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def source_info(self):
        return {"source_id": "bounded.test", "display_name": "Borne"}

    def discover(self, *args, **kwargs):
        return ()

    def collect(self, *, cursor=None, limit=50):
        self.calls.append((cursor, limit))
        return self.payload


def test_all_invalid_over_limit_page_does_not_erase_previous_success():
    plugin = LiteraryCriticismPlugin()
    plugin.index.ingest("bounded.test", [article("stable")], fresh_for_seconds=60)
    initial = plugin.index.articles()
    freshness = plugin.index.fresh_until("bounded.test")
    feed = Feed({"items": [article(i) for i in range(4)]})
    failure = plugin.collect_source(feed, limit=3, replace_source=True)
    assert failure.errors == ("collect: limit_exceeded",)
    assert failure.fresh_until == freshness
    assert feed.calls == [(None, 3)]
    assert plugin.index.articles() == initial


def test_feed_generator_is_not_drained_beyond_bound_plus_one():
    count = [0]

    def endless():
        n = 0
        while True:
            count[0] += 1
            yield article(n)
            n += 1

    plugin = LiteraryCriticismPlugin()
    report = plugin.collect_source(Feed({"items": endless()}), limit=4)
    assert report.errors == ("collect: limit_exceeded",)
    assert count == [5]
    assert not plugin.index.articles()


def test_exact_page_limit_is_accepted_and_source_freshness_is_capped():
    plugin = LiteraryCriticismPlugin()
    report = plugin.collect_source(
        Feed({"items": (article(i) for i in range(3)),
              "next_cursor": "cursor-next", "fresh_for_seconds": 10 ** 20}),
        limit=3,
    )
    assert report.accepted == 3
    assert not report.errors and report.next_cursor == "cursor-next"
    assert report.fresh_until == plugin.index.fresh_until("bounded.test")
    # No expiry overflow nor endless cache freshness.
    from datetime import datetime, timezone
    expiry = datetime.fromisoformat(report.fresh_until)
    delta = (expiry - datetime.now(timezone.utc)).total_seconds()
    assert 0 < delta <= 7 * 24 * 3600


def test_invalid_null_and_non_iterable_pages_are_not_empty_refreshes():
    for items in (None, "not-items", b"not-items", {"url": "not-a-list"}, 42):
        plugin = LiteraryCriticismPlugin()
        plugin.index.ingest("bounded.test", [article("stable")])
        saved = plugin.index.articles()
        report = plugin.collect_source(Feed({"items": items}), replace_source=True)
        assert report.errors, items
        assert plugin.index.articles() == saved


def test_empty_valid_page_still_allows_replace_source():
    plugin = LiteraryCriticismPlugin()
    plugin.index.ingest("bounded.test", [article("stable")])
    report = plugin.collect_source(Feed({"items": [], "next_cursor": None}),
                                   replace_source=True)
    assert not report.errors and not plugin.index.articles()


def test_malformed_cursor_and_secret_do_not_mutate_or_leak():
    plugin = LiteraryCriticismPlugin()
    plugin.index.ingest("bounded.test", [article("stable")])
    saved = plugin.index.articles()
    for cursor in ("X" * 600, "bearer\nSECRET", 5, {"secret": "PASSWORD"}):
        result = plugin.collect_source(
            Feed({"items": [article("other")], "next_cursor": cursor}),
            replace_source=True,
        )
        assert result.errors and "SECRET" not in repr(result)
        assert "PASSWORD" not in repr(result)
        assert plugin.index.articles() == saved


def test_exactly_one_more_than_declared_limit_rejects_even_valid_records():
    plugin = LiteraryCriticismPlugin()
    report = plugin.collect_source(
        Feed({"items": [article("one"), article("two")]}), limit=1,
    )
    assert report.accepted == 0 and report.errors == ("collect: limit_exceeded",)
    assert not plugin.index.articles()
