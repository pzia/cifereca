"""#1916: cached federated searches must retain successful empty providers."""
from __future__ import annotations

from tests.test_core043_progress_cache_stats import _service, _FakeSession


def test_all_empty_provider_responses_stay_visible_on_cache_hit(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        calls = []
        a, b = _FakeSession("source_a"), _FakeSession("source_b")
        for session in (a, b):
            session.search = lambda query, scope, *, field="all", content_types=(), pid=session.provider_id: (
                calls.append(pid) or []
            )
        service._sessions = {"source_a": a, "source_b": b}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        initial = []
        first = service.search_progressive("Metropolitan", force=True, on_batch=initial.append)
        assert first.providers_total == first.providers_completed == 2
        assert first.result_count == 0
        assert {b.provider_id: b.result_status for b in initial} == {
            "source_a": "live", "source_b": "live",
        }
        original_calls = list(calls)
        replayed = []
        cached = service.search_progressive("Metropolitan", force=False, on_batch=replayed.append)
        assert calls == original_calls  # A cache hit cannot issue provider I/O.
        assert cached.providers_total == cached.providers_completed == 2
        assert cached.result_count == cached.providers_failed == 0
        assert {b.provider_id: b.result_status for b in replayed} == {
            "source_a": "cached", "source_b": "cached",
        }
        assert all(b.cached and b.results == () for b in replayed)
    finally:
        service.close()


def test_mixed_nonempty_and_empty_cached_sources_keep_both_verdicts(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        empty = _FakeSession("empty")
        empty.search = lambda *_args, **_kwargs: []
        service._sessions = {"good": _FakeSession("good"), "empty": empty}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        first = service.search_progressive("Metropolitan", force=True)
        assert first.providers_completed == 2
        replayed = []
        cached = service.search_progressive("Metropolitan", on_batch=replayed.append)
        assert cached.providers_total == cached.providers_completed == 2
        assert cached.result_count == 1
        assert {b.provider_id: (b.result_status, len(b.results)) for b in replayed} == {
            "empty": ("cached", 0), "good": ("cached", 1),
        }
    finally:
        service.close()


def test_cached_delivery_interrupted_by_identity_switch_counts_only_delivered_sources(tmp_path, monkeypatch):
    from dataclasses import replace

    service = _service(tmp_path)
    try:
        service.identities.append(replace(service.current_identity, identity_id="bob", label="Bob"))
        a, b = _FakeSession("alpha"), _FakeSession("beta")
        a.search = b.search = lambda *_args, **_kwargs: []
        service._sessions = {"alpha": a, "beta": b}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        service.search_progressive("Metropolitan", force=True)
        callbacks = []
        def on_batch(batch):
            callbacks.append(batch)
            service.select_identity("bob")
        result = service.search_progressive("Metropolitan", on_batch=on_batch)
        assert result.cancelled is True
        assert result.providers_total == 2
        assert result.providers_completed == 1
        assert len(callbacks) == 1
        assert service.last_search == [] and service.last_search_warnings == []
    finally:
        service.close()
