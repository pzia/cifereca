import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

from recommendation_service import LibrarySnapshotCache, compute_recommendations


class Metadata:
    def __init__(self):
        self.calls = []

    def library_entries(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["identity_id"] == "id-1"
        return [
            {"title": "Seed 1", "author": "Alice", "metadata": {"subjects": ["histoire"]}},
            {"title": "Seed 2", "author": "Alice", "metadata": {"subjects": ["mémoire"]}},
        ]


class Catalog:
    def searchable_providers(self):
        return [("opds", "OPDS test")]

    def search(self, query, **kwargs):
        assert kwargs["force"] is False
        return [{"title": f"Candidate {query}", "author": "Alice", "metadata": {"subjects": ["histoire"]}, "source_provider": "opds"}]


class ProgressiveCatalog:
    def __init__(self):
        self.calls = []

    def searchable_providers(self):
        return [("a", "Catalogue A"), ("b", "Catalogue B")]

    def search_progressive(self, query, *, on_batch, **kwargs):
        self.calls.append((query, kwargs))
        assert kwargs["force"] is False
        assert kwargs["scope"] == "digital"
        on_batch(SimpleNamespace(
            provider_id="a", results=({"title": f"{query} A", "author": "Alice", "source_provider": "a"},),
            warnings=(), error_message="", cached=True,
        ))
        on_batch(SimpleNamespace(
            provider_id="b", results=({"title": f"{query} B", "author": "Alice", "source_provider": "b"},),
            warnings=(), error_message="", cached=False,
        ))
        return SimpleNamespace(warnings=(), cancelled=False)


def test_service_uses_public_brokers_and_returns_results():
    metadata = Metadata()
    result = compute_recommendations(metadata, Catalog(), "id-1", max_queries=3)
    assert metadata.calls == [{"identity_id": "id-1", "scan": False}]
    assert result.books_count == 2
    assert result.providers == (("opds", "OPDS test"),)
    assert result.ranked
    assert result.cache_unknown_calls == len(result.queries)


def test_progressive_search_reports_cache_network_and_progress():
    events = []
    catalog = ProgressiveCatalog()
    result = compute_recommendations(Metadata(), catalog, "id-1", max_queries=2, progress=lambda *args: events.append(args))
    assert len(catalog.calls) == len(result.queries)
    assert result.cache_batches == len(result.queries)
    assert result.network_batches == len(result.queries)
    assert result.cache_unknown_calls == 0
    assert events
    assert events[-1][0] == events[-1][1]
    assert events[-1][2] == ""
    assert any("catalogues 1/2" in event[2] for event in events)
    assert any("cache 1 / réseau 0" in event[2] for event in events)


def test_progressive_provider_rows_are_fairly_interleaved_before_limit():
    class ThreeResults(ProgressiveCatalog):
        def search_progressive(self, query, *, on_batch, **kwargs):
            on_batch(SimpleNamespace(
                provider_id="a",
                results=tuple({"title": f"A{i}", "author": "Alice", "source_provider": "a"} for i in range(4)),
                warnings=(), error_message="", cached=True,
            ))
            on_batch(SimpleNamespace(
                provider_id="b",
                results=tuple({"title": f"B{i}", "author": "Alice", "source_provider": "b"} for i in range(4)),
                warnings=(), error_message="", cached=True,
            ))
            return SimpleNamespace(warnings=(), cancelled=False)

    result = compute_recommendations(Metadata(), ThreeResults(), "id-1", max_queries=1, per_query=4, max_results=10, exploration=0)
    titles = {row.item["title"] for row in result.ranked}
    assert any(title.startswith("A") for title in titles)
    assert any(title.startswith("B") for title in titles)


def test_progressive_and_final_rankings_keep_all_credential_bound_notices():
    class MultiAccountCatalog(Catalog):
        def search_progressive(self, query, *, on_batch, **kwargs):
            common = {
                "title": "Candidate", "author": "Alice",
                "isbn": "9780306406157",
                "metadata": {"subjects": ["histoire"]},
                "source_provider": "opds", "record_id": "record-1",
            }
            for credential in ("account-a", "account-b"):
                on_batch(SimpleNamespace(
                    provider_id="opds",
                    results=({**common, "source_credential": credential},),
                    warnings=(), error_message="", cached=True,
                ))
            return SimpleNamespace(warnings=(), cancelled=False)

    events = []
    result = compute_recommendations(
        Metadata(), MultiAccountCatalog(), "id-1",
        max_queries=1, exploration=0, partial=events.append,
    )

    assert len(events) == 2
    assert [n.source_credential for n in events[0].ranked[0].notices] == ["account-a"]
    assert {n.source_credential for n in events[1].ranked[0].notices} == {
        "account-a", "account-b"
    }
    assert len(result.ranked) == 1
    assert {n.source_credential for n in result.ranked[0].notices} == {
        "account-a", "account-b"
    }


def test_performance_trace_is_sanitized_and_has_phase_timings(caplog):
    with caplog.at_level(logging.INFO, logger="cifereca.plugin.recommendations"):
        result = compute_recommendations(Metadata(), Catalog(), "id-1", max_queries=2)

    rows = [
        record.getMessage()
        for record in caplog.records
        if record.name == "cifereca.plugin.recommendations"
        and "TRACE recommendations performance" in record.getMessage()
    ]
    assert len(rows) == 1
    trace = rows[0]
    assert f"books={result.books_count}" in trace
    assert f"queries={len(result.queries)}" in trace
    assert f"providers={len(result.providers)}" in trace
    for field in ("snapshot_ms=", "snapshot_cached=", "profile_ms=", "providers_ms=", "catalog_ms=", "rank_ms=", "total_ms="):
        assert field in trace
    assert "id-1" not in trace
    assert "Seed 1" not in trace
    assert "Candidate" not in trace


class OwnedCatalog:
    def searchable_providers(self):
        return [("opds", "OPDS test")]

    def search(self, query, **kwargs):
        return [{
            "title": "Seed 1",
            "author": "Alice",
            "metadata": {"subjects": ["histoire"]},
            "source_provider": "opds",
        }]


def test_service_can_include_owned_books_when_filter_is_disabled():
    hidden = compute_recommendations(Metadata(), OwnedCatalog(), "id-1", max_queries=2)
    shown = compute_recommendations(
        Metadata(),
        OwnedCatalog(),
        "id-1",
        max_queries=2,
        exclude_owned=False,
    )
    assert hidden.ranked == ()
    assert shown.ranked
    assert shown.ranked[0].item["title"] == "Seed 1"


def test_service_returns_already_loaded_profile_books_without_second_snapshot():
    metadata = Metadata()
    result = compute_recommendations(metadata, Catalog(), "id-1", max_queries=2)

    assert metadata.calls == [{"identity_id": "id-1", "scan": False}]
    assert [book["title"] for book in result.profile_books] == ["Seed 1", "Seed 2"]


def test_snapshot_cache_reuses_library_projection_until_invalidated():
    metadata = Metadata()
    cache = LibrarySnapshotCache()

    first = compute_recommendations(
        metadata, Catalog(), "id-1", max_queries=2, snapshot_cache=cache
    )
    second = compute_recommendations(
        metadata, Catalog(), "id-1", max_queries=2, snapshot_cache=cache
    )

    assert len(metadata.calls) == 1
    assert first.profile_books == second.profile_books

    cache.invalidate()
    compute_recommendations(
        metadata, Catalog(), "id-1", max_queries=2, snapshot_cache=cache
    )
    assert len(metadata.calls) == 2


def test_snapshot_cache_reloads_if_invalidated_during_inflight_read():
    class BlockingMetadata:
        def __init__(self):
            self.calls = 0
            self.first_read_started = Event()
            self.release_first_read = Event()

        def library_entries(self, **kwargs):
            assert kwargs == {"identity_id": "id-1", "scan": False}
            self.calls += 1
            if self.calls == 1:
                self.first_read_started.set()
                assert self.release_first_read.wait(timeout=2)
                return [{"title": "Stale", "author": "Alice"}]
            return [{"title": "Fresh", "author": "Alice"}]

    metadata = BlockingMetadata()
    cache = LibrarySnapshotCache()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(cache.load, metadata, "id-1")
        assert metadata.first_read_started.wait(timeout=2)

        cache.invalidate("id-1")
        metadata.release_first_read.set()
        snapshot = future.result(timeout=2)

    assert metadata.calls == 2
    assert snapshot.books[0]["title"] == "Fresh"
    assert snapshot.cached is False
    assert cache._inflight == {}

    cached = cache.load(metadata, "id-1")
    assert cached.cached is True
    assert cached.books == snapshot.books
    assert metadata.calls == 2


def test_snapshot_cache_can_invalidate_one_identity_without_dropping_others():
    class MultiMetadata:
        def __init__(self):
            self.calls = []

        def library_entries(self, **kwargs):
            identity_id = kwargs["identity_id"]
            self.calls.append(identity_id)
            return [{"title": f"Seed {identity_id}", "author": "Alice", "metadata": {"subjects": ["histoire"]}}]

    metadata = MultiMetadata()
    cache = LibrarySnapshotCache()
    cache.load(metadata, "id-1")
    cache.load(metadata, "id-2")
    cache.load(metadata, "id-1")
    cache.load(metadata, "id-2")
    assert metadata.calls == ["id-1", "id-2"]

    cache.invalidate("id-1")
    cache.load(metadata, "id-1")
    cache.load(metadata, "id-2")
    assert metadata.calls == ["id-1", "id-2", "id-1"]
