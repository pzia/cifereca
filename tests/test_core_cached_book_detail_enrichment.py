from __future__ import annotations

import time
from threading import Event
from types import SimpleNamespace as NS

from cifereca.detail_enrichment import CachedDetailCollector, DETAIL_ENRICHMENT_CATEGORY
from cifereca.plugin_api import (
    BookRef, BookDetailCacheEnricher, BookDetailCacheNotifier, CachedBookDetail,
    DetailField, DetailSection, Provenance, UiBookDetailAction,
)


BOOK = BookRef("book-42", "Test title", ("Author",), {"isbn": "9780306406157"})


def payload(source="Source", *, book_id="book-42", identity="alice", stale=False,
            sections=None, actions=()):
    if sections is None:
        sections = (DetailSection(
            "summary", "Avis", (DetailField("n", "Critiques", "3"),),
        ),)
    return CachedBookDetail(
        book_id, identity, sections, tuple(actions),
        Provenance(source, observed_at="2026-10-08T19:00:00Z"),
        "2026-10-08T19:00:00Z", "cache-v2", stale,
    )


def resolved(plugin_id, service):
    return NS(plugin_id=plugin_id, service=service, priority=999)


class Cached:
    def __init__(self, value=None):
        self.value = value
        self.calls = []

    def cached_detail(self, book, *, identity_id):
        self.calls.append((book, identity_id))
        return self.value


class Notifying(Cached):
    def __init__(self, value):
        super().__init__(value)
        self.listeners = []

    def subscribe_cache(self, callback):
        self.listeners.append(callback)
        def unsubscribe():
            self.listeners.remove(callback)
        return unsubscribe


def test_public_contract_is_optional_and_cache_only():
    assert DETAIL_ENRICHMENT_CATEGORY == "book.detail.enrichment"
    service = Cached(payload())
    assert isinstance(service, BookDetailCacheEnricher)
    assert not isinstance(service, BookDetailCacheNotifier)
    assert isinstance(Notifying(payload()), BookDetailCacheNotifier)
    assert service.cached_detail(BOOK, identity_id="alice").book_id == BOOK.book_id


def test_empty_cache_has_no_contribution():
    collector = CachedDetailCollector()
    try:
        assert collector.collect([resolved("empty", Cached())], BOOK, identity_id="alice") == ()
    finally:
        collector.close()


def test_multiple_plugins_are_stable_and_keep_attested_freshness():
    a, b = Cached(payload("La Presse")), Cached(payload("Bibliographie", stale=True))
    collector = CachedDetailCollector()
    try:
        views = collector.collect([resolved("z.plugin", a), resolved("a.plugin", b)],
                                  BOOK, identity_id="alice")
        assert len(views) == 2
        assert views[0].sections[0].id == "a.plugin:summary"
        assert views[1].sections[0].id == "z.plugin:summary"
        assert views[0].sections[0].title == "Avis · Bibliographie · périmé"
        assert views[1].sections[0].title == "Avis · La Presse"
        assert views[0].freshness_token == "cache-v2"
        assert views[1].source.observed_at == "2026-10-08T19:00:00Z"
        assert a.calls == [(BOOK, "alice")]
        assert b.calls == [(BOOK, "alice")]
    finally:
        collector.close()


def test_wrong_identity_or_book_is_never_displayed():
    collector = CachedDetailCollector()
    try:
        services = [resolved("one", Cached(payload(identity="bob"))),
                    resolved("two", Cached(payload(book_id="book-elsewhere")))]
        assert collector.collect(services, BOOK, identity_id="alice") == ()
    finally:
        collector.close()


def test_one_failing_plugin_does_not_block_healthy_one():
    class Broken:
        def cached_detail(self, book, *, identity_id):
            raise RuntimeError("unexpected third party failure")

    collector = CachedDetailCollector()
    try:
        views = collector.collect([resolved("broken", Broken()),
                                   resolved("ok", Cached(payload()))],
                                  BOOK, identity_id="alice")
        assert len(views) == 1
        assert views[0].sections[0].id == "ok:summary"
    finally:
        collector.close()


def test_slow_plugin_is_bounded_and_not_resubmitted_while_running():
    started, finish = Event(), Event()
    class Slow:
        calls = 0
        def cached_detail(self, book, *, identity_id):
            self.calls += 1
            started.set()
            finish.wait(0.3)
            return payload()

    slow = Slow()
    collector = CachedDetailCollector(max_workers=1)
    try:
        begin = time.monotonic()
        assert collector.collect([resolved("slow", slow)], BOOK,
                                 identity_id="alice", budget_ms=25) == ()
        assert time.monotonic() - begin < 0.22
        assert started.wait(0.2)
        assert collector.collect([resolved("slow", slow)], BOOK,
                                 identity_id="alice", budget_ms=25) == ()
        assert slow.calls == 1
    finally:
        finish.set()
        collector.close()


def test_fields_and_actions_are_bounded_and_sanitized():
    sections = tuple(
        DetailSection(f"s-{n}", "Sélection\n  spéciale", tuple(
            DetailField(f"f-{i}", f"Champ {i}", "é" * 600, kind="html")
            for i in range(12)
        )) for n in range(6)
    )
    actions = (
        UiBookDetailAction("uri", "Voir le site", uri="https://example.com/review/42"),
        UiBookDetailAction("invalid", "Jeton", uri="https://example.com/download?token=secret"),
        UiBookDetailAction("other", "Ouvrir", callback=lambda: None),
    )
    collector = CachedDetailCollector()
    try:
        (value,) = collector.collect(
            [resolved("critic", Cached(payload(sections=sections, actions=actions)))],
            BOOK, identity_id="alice",
        )
        assert len(value.sections) == 2
        assert len(value.sections[0].fields) == 5
        assert value.sections[0].fields[0].kind == "text"
        assert len(value.sections[0].fields[0].value) == 280
        assert "\n" not in value.sections[0].title
        assert [action.id for action in value.actions] == ["critic:uri"]
    finally:
        collector.close()


def test_subscription_can_be_cancelled_and_broken_notifiers_are_isolated():
    one, two = Notifying(payload()), Notifying(payload())
    class BadNotifier:
        def subscribe_cache(self, callback):
            raise RuntimeError("bad subscription")

    collector = CachedDetailCollector()
    calls = []
    try:
        cancel = collector.subscribe(
            [resolved("one", one), resolved("one-duplicate", one),
             resolved("bad", BadNotifier()), resolved("two", two)],
            lambda: calls.append("changed"),
        )
        assert len(one.listeners) == len(two.listeners) == 1
        one.listeners[0]()
        assert calls == ["changed"]
        cancel()
        cancel()
        assert one.listeners == two.listeners == []
    finally:
        collector.close()
