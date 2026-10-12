"""P2 #709: source refresh must withdraw stale bibliographic proofs.

All snippets are synthetic; no network, credentials or private book data.
"""
from __future__ import annotations

from literary_criticism import CriticismIndex, LiteraryCriticismPlugin

SHARED = "https://reviews.example/article/shared"
OLD_ISBN = "9780306406157"
NEW_ISBN = "9780140328721"
OLD_NOTICE = "https://catalog.example/old-edition"
NEW_NOTICE = "https://catalog.example/new-edition"


def article(*, isbn="", notice="", book_author="Autrice", excerpt="Court"):
    return {
        "title": "Une critique",
        "url": SHARED,
        "book_title": "Livre",
        "book_author": book_author,
        "isbn": isbn,
        "notice_url": notice,
        "excerpt": excerpt,
    }


def test_replacing_one_source_with_same_url_withdraws_old_isbn_and_notice():
    index = CriticismIndex()
    index.ingest("feed.a", [article(isbn=OLD_ISBN, notice=OLD_NOTICE)], replace_source=True)
    old = index.articles()[0]
    assert old.isbns == (OLD_ISBN,)
    assert old.notice_urls == (OLD_NOTICE,)
    assert index.matches_for({"title": "Autre livre", "isbn": OLD_ISBN})[0].status == "confirmed"

    index.ingest("feed.a", [article(isbn=NEW_ISBN, notice=NEW_NOTICE)], replace_source=True)
    current = index.articles()[0]
    assert current.isbns == (NEW_ISBN,)
    assert current.notice_urls == (NEW_NOTICE,)
    assert current.provenance == ("feed.a",)
    assert index.matches_for({"title": "Autre livre", "isbn": OLD_ISBN}) == ()
    assert index.matches_for({"title": "Autre livre", "friendly_url": OLD_NOTICE}) == ()
    assert index.matches_for({"title": "Autre livre", "isbn": NEW_ISBN})[0].status == "confirmed"


def test_removing_one_of_two_sources_does_not_leak_withdrawn_evidence():
    index = CriticismIndex()
    index.ingest("feed.a", [article(isbn=OLD_ISBN, notice=OLD_NOTICE)], replace_source=True)
    index.ingest("feed.b", [article(isbn=NEW_ISBN, notice=NEW_NOTICE)], replace_source=True)
    both = index.articles()[0]
    assert both.provenance == ("feed.a", "feed.b")
    assert set(both.isbns) == {OLD_ISBN, NEW_ISBN}
    assert set(both.notice_urls) == {OLD_NOTICE, NEW_NOTICE}

    index.ingest("feed.a", [], replace_source=True)
    retained = index.articles()[0]
    assert retained.provenance == ("feed.b",)
    assert retained.source_id == "feed.b"
    assert retained.isbns == (NEW_ISBN,)
    assert retained.notice_urls == (NEW_NOTICE,)
    assert index.matches_for({"isbn": OLD_ISBN}) == ()
    assert index.matches_for({"isbn": NEW_ISBN})[0].status == "confirmed"


def test_one_source_replacement_does_not_remove_other_source_evidence():
    index = CriticismIndex()
    index.ingest("feed.a", [article(isbn=OLD_ISBN)], replace_source=True)
    index.ingest("feed.b", [article(isbn=NEW_ISBN)], replace_source=True)
    index.ingest("feed.a", [article(notice=OLD_NOTICE)], replace_source=True)
    current = index.articles()[0]
    assert current.provenance == ("feed.a", "feed.b")
    assert current.isbns == (NEW_ISBN,)
    assert current.notice_urls == (OLD_NOTICE,)
    assert index.matches_for({"isbn": OLD_ISBN}) == ()
    assert index.matches_for({"isbn": NEW_ISBN})[0].status == "confirmed"


def test_incremental_pages_preserve_earlier_proofs_from_same_source():
    index = CriticismIndex()
    index.ingest("feed.a", [article(isbn=OLD_ISBN)])
    report = index.ingest("feed.a", [article(notice=OLD_NOTICE)])
    assert report.duplicates == 1
    current = index.articles()[0]
    assert current.isbns == (OLD_ISBN,)
    assert current.notice_urls == (OLD_NOTICE,)
    assert current.provenance == ("feed.a",)


def test_invalid_refresh_preserves_per_source_evidence_and_freshness():
    index = CriticismIndex()
    first = index.ingest(
        "feed.a", [article(isbn=OLD_ISBN)], replace_source=True,
        fresh_for_seconds=600,
    )
    index.ingest("feed.b", [article(isbn=NEW_ISBN)])
    before = index.articles()
    report = index.ingest(
        "feed.a", [{"title": "No URL", "secret": "TOKEN=private"}],
        replace_source=True, fresh_for_seconds=900,
    )
    assert report.errors == ("item[0]: invalid_article",)
    assert report.fresh_until == first.fresh_until
    assert index.articles() == before
    assert index.matches_for({"isbn": OLD_ISBN})[0].status == "confirmed"
    assert "TOKEN" not in repr(report)


def test_valid_empty_refresh_invalidates_stale_freshness():
    index = CriticismIndex()
    first = index.ingest(
        "feed.a", [article(isbn=OLD_ISBN)], replace_source=True,
        fresh_for_seconds=900,
    )
    assert first.fresh_until
    report = index.ingest("feed.a", [], replace_source=True)
    assert report.fresh_until == ""
    assert index.fresh_until("feed.a") == ""
    assert index.articles() == ()


def test_disjoint_sources_and_changed_author_do_not_pollute_match():
    index = CriticismIndex()
    index.ingest("feed.a", [article(book_author="Ancienne autrice")], replace_source=True)
    index.ingest("feed.b", [article(isbn=NEW_ISBN)], replace_source=True)
    index.ingest("feed.a", [article(book_author="Nouvelle autrice")], replace_source=True)
    current = index.articles()[0]
    assert "Ancienne autrice" not in current.book_authors
    assert "Nouvelle autrice" in current.book_authors


class Feed:
    def __init__(self, rows):
        self.rows = rows

    def source_info(self):
        return {"source_id": "feed.a", "display_name": "Feed A"}

    def discover(self, query, limit=20):
        return ()

    def collect(self, cursor=None, limit=50):
        return {"items": self.rows, "next_cursor": ""}


def test_source_contract_refresh_uses_only_last_page_authority():
    plugin = LiteraryCriticismPlugin()
    plugin.collect_source(Feed([article(isbn=OLD_ISBN)]), replace_source=True)
    report = plugin.collect_source(Feed([article(isbn=NEW_ISBN)]), replace_source=True)
    assert report.accepted == 0 and report.duplicates == 1
    assert plugin.index.articles()[0].isbns == (NEW_ISBN,)
    assert plugin.articles_for({"isbn": OLD_ISBN}) == ()
