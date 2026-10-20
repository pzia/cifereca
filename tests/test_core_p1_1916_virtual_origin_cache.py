"""#1916: virtual provider cache cannot silently drop origin-attributed books."""
from __future__ import annotations

from dataclasses import replace

from tests.test_core043_progress_cache_stats import _service, _FakeSession


def _source(name, *, origin, calls):
    session = _FakeSession(name)
    original = session.search
    def query(*args, **kwargs):
        calls.append(name)
        return [replace(book, source_provider=origin) for book in original(*args, **kwargs)]
    session.search = query
    return session


def test_single_virtual_session_cache_replays_origin_rows_on_the_selected_batch(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        calls = []
        service._sessions = {"virtual": _source("virtual", origin="origin.bookstore", calls=calls)}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        first = service.search_progressive("Metropolitan", force=True)
        assert first.result_count == first.providers_completed == 1
        seen = []
        cached = service.search_progressive("Metropolitan", on_batch=seen.append)
        assert calls == ["virtual"]
        assert cached.result_count == 1 and cached.providers_completed == 1
        assert len(seen) == 1
        assert seen[0].provider_id == "virtual"
        assert seen[0].cached and seen[0].result_status == "cached"
        assert len(seen[0].results) == 1
        assert seen[0].results[0].source_provider == "origin.bookstore"
        assert len(service.last_search) == 1
    finally:
        service.close()


def test_multi_session_ambiguous_origin_cache_is_recomputed_not_silently_dropped(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        calls = []
        service._sessions = {
            "virtual_a": _source("virtual_a", origin="origin.bookstore", calls=calls),
            "virtual_b": _source("virtual_b", origin="origin.other", calls=calls),
        }
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        first = service.search_progressive("Metropolitan", force=True)
        assert first.providers_completed == 2 and first.result_count == 2
        assert len(calls) == 2
        batches = []
        second = service.search_progressive("Metropolitan", on_batch=batches.append)
        assert len(calls) == 4  # cached list has no trustworthy session ownership
        assert second.providers_completed == 2 and second.result_count == 2
        assert {b.provider_id for b in batches} == {"virtual_a", "virtual_b"}
        assert all(not b.cached and len(b.results) == 1 for b in batches)
    finally:
        service.close()


def test_virtual_origin_stale_cached_batch_keeps_provenance(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        calls = []
        session = _source("virtual", origin="origin.bookstore", calls=calls)
        original_search = session.search
        def stale(*args, **kwargs):
            return [replace(row, metadata={**(row.metadata or {}),
                                           "search_provenance": "known_catalog_stale"})
                    for row in original_search(*args, **kwargs)]
        session.search = stale
        service._sessions = {"virtual": session}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        service.search_progressive("Metropolitan", force=True)
        seen = []
        service.search_progressive("Metropolitan", on_batch=seen.append)
        assert calls == ["virtual"]
        assert len(seen) == 1 and seen[0].cached
        assert seen[0].provider_id == "virtual"
        assert seen[0].result_status == "known_catalog_stale"
        assert seen[0].results[0].source_provider == "origin.bookstore"
        assert seen[0].results[0].metadata["search_provenance"] == "known_catalog_stale"
    finally:
        service.close()
