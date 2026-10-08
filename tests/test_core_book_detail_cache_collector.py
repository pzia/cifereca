from __future__ import annotations

import threading
import time

from cifereca.plugin_api import (
    BookRef, BookDetailCacheEnricher, CachedBookDetail, DetailField,
    DetailSection, UiBookDetailAction, ResolvedPluginService, Provenance,
    CachedBookDetailContribution, collect_cached_book_details,
    subscribe_cached_book_detail_changes, contract_definition,
)
from cifereca.plugins.categories import CategoryRegistry


BOOK = BookRef(book_id="book-42", title="Livre")


def _resolved(plugin_id: str, service: object) -> ResolvedPluginService:
    return ResolvedPluginService(plugin_id, "1", "bundle", "1", "book.detail.enrichment", service)


class Cache:
    def __init__(self, detail):
        self.detail = detail
        self.calls = []

    def cached_detail(self, book, *, identity_id):
        self.calls.append((book.book_id, identity_id))
        return self.detail


def _detail(*, book_id="book-42", identity_id="alice", sections=None, actions=()):
    if sections is None:
        sections = (DetailSection("review", "Critiques", (
            DetailField("count", "Nombre", 3),
        )),)
    return CachedBookDetail(
        book_id=book_id, identity_id=identity_id,
        sections=tuple(sections), actions=tuple(actions),
        updated_at="2026-10-08", freshness_token="snapshot-2",
    )


def test_public_category_is_registered_as_all_and_enforces_cache_protocol():
    category = CategoryRegistry().get("book.detail.enrichment")
    assert category.resolution == "all"
    assert category.contract_id == "cifereca.book.detail.enrichment"
    assert contract_definition(category.id).protocol_name == "BookDetailCacheEnricher"
    from cifereca.plugin_api import check_service_contract
    assert check_service_contract(category.id, Cache(None)).ok
    assert not check_service_contract(category.id, object()).ok


def test_two_plugin_contributions_have_stable_ids_and_source():
    cache_a, cache_z = Cache(_detail()), Cache(_detail())
    result = collect_cached_book_details([
        _resolved("zeta", cache_z), _resolved("alpha", cache_a),
    ], BOOK, identity_id="alice")
    assert tuple(x.plugin_id for x in result) == ("alpha", "zeta")
    assert all(isinstance(x, CachedBookDetailContribution) for x in result)
    assert result[0].detail.sections[0].title == "Critiques"
    assert result[0].detail.source == Provenance(source="alpha")
    assert result[1].detail.freshness_token == "snapshot-2"
    assert cache_a.calls == [("book-42", "alice")]


def test_wrong_identity_wrong_book_and_empty_cache_are_not_published():
    entries = [
        _resolved("wrong-book", Cache(_detail(book_id="other"))),
        _resolved("wrong-identity", Cache(_detail(identity_id="bob"))),
        _resolved("cache-empty", Cache(None)),
    ]
    assert collect_cached_book_details(entries, BOOK, identity_id="alice") == ()
    assert collect_cached_book_details(entries, BOOK, identity_id="") == ()
    assert collect_cached_book_details(entries, BookRef(book_id=""), identity_id="alice") == ()


def test_malformed_plugin_and_exception_are_isolated():
    class Broken:
        def cached_detail(self, book, *, identity_id):
            raise RuntimeError("broken cache")
    result = collect_cached_book_details([
        _resolved("bad", Broken()),
        _resolved("malformed", Cache({"book_id": "book-42"})),
        _resolved("good", Cache(_detail())),
        _resolved("inert", object()),
    ], BOOK, identity_id="alice")
    assert [entry.plugin_id for entry in result] == ["good"]


def test_bounded_sections_fields_and_actions_reject_unsafe_urls():
    many = tuple(DetailSection(f"s{i}", "X" * 200, tuple(
        DetailField(f"f{j}", "Label", "y" * 1000) for j in range(12)
    )) for i in range(9))
    actions = [
        UiBookDetailAction("bad", "Unsafe", uri="https://example.org/download/file.epub"),
        UiBookDetailAction("private", "Secret", uri="https://example.org/book?token=secret"),
        UiBookDetailAction("good", "Public", uri="https://example.org/notice/42"),
        UiBookDetailAction("callback", "Ouvrir", callback=lambda: None),
        UiBookDetailAction("over", "Over limit", callback=lambda: None),
    ]
    result = collect_cached_book_details([
        _resolved("reviewer", Cache(_detail(sections=many, actions=actions))),
    ], BOOK, identity_id="alice")
    assert len(result) == 1
    detail = result[0].detail
    assert len(detail.sections) == 3
    assert all(len(section.fields) == 8 for section in detail.sections)
    assert len(detail.sections[0].title) == 100
    assert len(detail.sections[0].fields[0].value) == 280
    assert [a.id for a in detail.actions] == ["good", "callback"]


def test_slow_plugin_cannot_delay_fast_one_past_budget():
    blocked = threading.Event()
    entered = threading.Event()

    class Slow:
        def cached_detail(self, book, *, identity_id):
            entered.set()
            blocked.wait(timeout=2)
            return _detail()

    start = time.monotonic()
    try:
        result = collect_cached_book_details([
            _resolved("a-fast", Cache(_detail())),
            _resolved("z-slow", Slow()),
        ], BOOK, identity_id="alice", budget_ms=30)
        elapsed = time.monotonic() - start
        assert elapsed < 0.30
        assert [x.plugin_id for x in result] == ["a-fast"]
    finally:
        blocked.set()


def test_only_four_contributors_are_inspected():
    caches = [Cache(_detail()) for _ in range(7)]
    result = collect_cached_book_details([
        _resolved(f"plugin-{i}", cache) for i, cache in enumerate(caches)
    ], BOOK, identity_id="alice")
    assert len(result) == 4
    assert all(not cache.calls for cache in caches[4:])


def test_cache_subscription_is_unsubscribed_and_errors_are_isolated():
    events = []
    class Notifier:
        def subscribe_cache(self, callback):
            events.append(("subscribe", callback))
            return lambda: events.append(("unsubscribe", callback))
    class Broken:
        def subscribe_cache(self, callback):
            raise ValueError("broken subscription")
    callback = lambda: events.append(("refresh", None))
    close = subscribe_cached_book_detail_changes([
        _resolved("b", Broken()),
        _resolved("a", Notifier()),
        _resolved("c", object()),
    ], callback)
    assert len(events) == 1
    events[0][1]()
    assert events[-1] == ("refresh", None)
    close()
    close()
    assert sum(x[0] == "unsubscribe" for x in events) == 1
