"""P2 #709: fail closed on malformed feeds and secret-bearing exceptions."""
from __future__ import annotations

from literary_criticism import CriticismIndex, LiteraryCriticismPlugin


def row(slug, *, isbn=""):
    return {
        "title": "Lecture de " + slug,
        "url": "https://literary.example/reviews/" + slug,
        "book_title": "Livre",
        "book_author": "Autrice",
        "isbn": isbn,
    }


def test_replacement_with_only_invalid_rows_preserves_last_good_cache_and_freshness():
    index = CriticismIndex()
    first = index.ingest("source.a", [row("a")], fresh_for_seconds=600)
    assert first.fresh_until
    old = index.articles()
    failed = index.ingest("source.a", [
        {"title": "sans URL", "description": "private bearer token"},
        {"url": "file:///secret", "title": "pas public"},
    ], replace_source=True, fresh_for_seconds=900)
    assert len(failed.errors) == 2
    assert all("bearer" not in msg for msg in failed.errors)
    assert failed.fresh_until == first.fresh_until
    assert index.fresh_until("source.a") == first.fresh_until
    assert index.articles() == old


def test_legitimately_empty_successful_replacement_still_clears_stale_reviews():
    index = CriticismIndex()
    index.ingest("source.a", [row("a")])
    cleared = index.ingest("source.a", [], replace_source=True)
    assert cleared.errors == ()
    assert index.articles() == ()


def test_all_invalid_one_source_does_not_erase_other_or_shared_provenance():
    index = CriticismIndex()
    index.ingest("source.a", [row("shared"), row("only-a")])
    index.ingest("source.b", [row("shared"), row("only-b")])
    before = index.articles()
    index.ingest("source.a", [{"title": "invalid"}], replace_source=True)
    assert index.articles() == before
    index.ingest("source.a", [], replace_source=True)
    assert {article.url.rsplit("/", 1)[-1] for article in index.articles()} == {
        "shared", "only-b",
    }
    shared = next(x for x in index.articles() if x.url.endswith("shared"))
    assert shared.provenance == ("source.b",)


class SecretValue:
    def __str__(self):
        raise RuntimeError("Bearer-PRIVATE user-email@example.com")


def test_malformed_article_error_never_echoes_private_exception():
    index = CriticismIndex()
    result = index.ingest("source.a", [
        {"title": SecretValue(), "url": "https://literary.example/secret"}
    ])
    assert result.errors == ("item[0]: invalid_article",)
    assert "Bearer" not in repr(result)


class BrokenSource:
    def source_info(self):
        return {"source_id": "source.secret", "display_name": "Test"}

    def discover(self, *_args, **_kwargs):
        return ()

    def collect(self, **_kwargs):
        raise RuntimeError("access_token=TOP_SECRET&account=private")


def test_collect_errors_do_not_expose_sensitive_vendor_messages():
    plugin = LiteraryCriticismPlugin()
    report = plugin.collect_source(BrokenSource())
    assert report.errors == ("collect: source_error",)
    assert "TOP_SECRET" not in repr(report)
    assert plugin.index.articles() == ()
