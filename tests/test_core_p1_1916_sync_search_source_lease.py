"""#1916: sync search cannot publish results from a disconnected/rebound source.

Actual Core service and SQLite, synthetic providers; no external network.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import threading

import pytest

from tests.test_core043_progress_cache_stats import _service, _FakeSession


@pytest.mark.parametrize("transition", ["replace", "credential", "disconnect"])
def test_inflight_sync_provider_rebind_cannot_publish_or_cache(
    tmp_path, monkeypatch, transition,
):
    service = _service(tmp_path)
    session = _FakeSession("good")
    service._sessions = {"good": session}
    entered, release = threading.Event(), threading.Event()
    recorded = []
    original = session.search

    def search_remote(*args, **kwargs):
        entered.set()
        assert release.wait(8)
        return original(*args, **kwargs)

    session.search = search_remote
    monkeypatch.setattr(
        service, "_mirror_remote_results",
        lambda rows, **kw: recorded.extend(rows),
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            old = pool.submit(service.search, "Progressive Book", force=True)
            assert entered.wait(5)
            if transition == "replace":
                # Same credential and provider, *different session* object.
                service._sessions["good"] = _FakeSession("good")
            elif transition == "credential":
                session.credential_id = "other-credential"
            else:
                session.connected = False
            release.set()
            assert old.result(timeout=8) == []
        assert service.last_search == []
        assert service.last_search_warnings == []
        assert recorded == []
        assert not any(
            row["namespace"] == "search"
            for row in service.storage.cache_namespace_stats()
        )
    finally:
        release.set()
        service.close()


def test_sync_cached_result_refused_if_source_replaced_during_cache_read(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    source = _FakeSession("good")
    service._sessions = {"good": source}
    try:
        original = service.search("Progressive Book", force=True)
        assert len(original) == 1
        service.last_search = []
        def rebind_on_cache_read(*_args, **_kwargs):
            service._sessions["good"] = _FakeSession("good")
            return [asdict(original[0])]
        monkeypatch.setattr(service.storage, "cache_get", rebind_on_cache_read)
        assert service.search("Progressive Book") == []
        assert service.last_search == []
    finally:
        service.close()


def test_sync_provider_disappearing_during_discovery_never_gets_queried(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    source = _FakeSession("good")
    service._sessions = {"good": source}
    requests = []
    source.search = lambda *_a, **_kw: requests.append(True) or []
    def change_registry(_provider_id):
        service._sessions["good"] = _FakeSession("good")
        return True
    monkeypatch.setattr(service, "_provider_enabled_for_identity", change_registry)
    try:
        assert service.search("Progressive Book", force=True) == []
        assert requests == []
        assert service.last_search == []
    finally:
        service.close()


def test_stable_sync_source_can_publish_and_hit_cache(tmp_path):
    service = _service(tmp_path)
    service._sessions = {"good": _FakeSession("good")}
    try:
        first = service.search("Progressive Book", force=True)
        assert first and len(first) == 1
        second = service.search("Progressive Book")
        assert second and len(second) == 1
        assert service.last_search == second
    finally:
        service.close()


@pytest.mark.parametrize("transition", ["replace", "credential", "disconnect"])
def test_progressive_provider_rebind_revokes_all_pending_batches(
    tmp_path, monkeypatch, transition,
):
    service = _service(tmp_path)
    source = _FakeSession("good")
    service._sessions = {"good": source}
    entered, release = threading.Event(), threading.Event()
    original = source.search
    sent = []
    mirrors = []

    def delayed(*a, **kw):
        entered.set()
        assert release.wait(8)
        return original(*a, **kw)

    source.search = delayed
    monkeypatch.setattr(
        service, "_mirror_remote_results",
        lambda rows, **kw: mirrors.extend(rows),
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                service.search_progressive, "Progressive Book",
                force=True, on_batch=sent.append,
            )
            assert entered.wait(5)
            if transition == "replace":
                service._sessions["good"] = _FakeSession("good")
            elif transition == "credential":
                source.credential_id = "other"
            else:
                source.connected = False
            release.set()
            summary = future.result(timeout=8)
        assert summary.cancelled is True
        assert sent == []
        assert mirrors == []
        assert service.last_search == []
        assert not any(
            row["namespace"] == "search"
            for row in service.storage.cache_namespace_stats()
        )
    finally:
        release.set()
        service.close()


def test_progressive_cached_hit_does_not_publish_after_source_rebind(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    source = _FakeSession("good")
    service._sessions = {"good": source}
    try:
        first = service.search_progressive("Progressive Book", force=True)
        assert first.result_count == 1 and not first.cancelled
        service.last_search = []
        def rebind_on_cache_read(*_args, **_kwargs):
            service._sessions["good"] = _FakeSession("good")
            return []
        monkeypatch.setattr(service.storage, "cache_get", rebind_on_cache_read)
        batches = []
        summary = service.search_progressive(
            "Progressive Book", on_batch=batches.append,
        )
        assert summary.cancelled is True
        assert batches == []
        assert service.last_search == []
    finally:
        service.close()


def test_progressive_stable_source_can_publish_and_cache(tmp_path):
    service = _service(tmp_path)
    service._sessions = {"good": _FakeSession("good")}
    try:
        batches = []
        summary = service.search_progressive(
            "Progressive Book", force=True, on_batch=batches.append,
        )
        assert not summary.cancelled
        assert summary.result_count == 1
        assert len(batches) == 1 and len(batches[0].results) == 1
        assert service.last_search == list(batches[0].results)
    finally:
        service.close()
