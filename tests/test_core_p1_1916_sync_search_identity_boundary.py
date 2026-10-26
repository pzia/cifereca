"""#1916: synchronous search is also generation-scoped.

Real service/SQLite with synthetic providers; no HTTP or private library I/O.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import threading

import pytest

from tests.test_core043_progress_cache_stats import _service, _FakeSession


def _rig(tmp_path):
    service = _service(tmp_path)
    service.identities.append(replace(
        service.current_identity, identity_id="bob", label="Bob",
    ))
    service._sessions = {"good": _FakeSession("good")}
    return service


@pytest.mark.parametrize("back_to_alice", [False, True])
def test_sync_search_profile_change_during_get_cannot_publish(
    tmp_path, monkeypatch, back_to_alice,
):
    service = _rig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    session = service._sessions["good"]
    original = session.search
    mirrored = []
    monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kw: mirrored.extend(rows))
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    session.search = delayed
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.search, "Progressive Book", force=True)
            assert entered.wait(5)
            service.select_identity("bob")
            if back_to_alice:
                service.select_identity("alice")
            sentinel = original("new", "all")[0]
            service.last_search = [sentinel]
            service.last_search_warnings = ["new profile state"]
            release.set()
            assert future.result(timeout=5) == []
        assert service.last_search == [sentinel]
        assert service.last_search_warnings == ["new profile state"]
        assert mirrored == []
        assert not any(
            row["namespace"] == "search"
            for row in service.storage.cache_namespace_stats()
        )
    finally:
        release.set()
        service.close()


def test_sync_search_does_not_publish_superseded_query(tmp_path, monkeypatch):
    service = _rig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    session = service._sessions["good"]
    original = session.search
    def selected(query, scope, **kwargs):
        if query == "Old Book":
            entered.set()
            assert release.wait(5)
        return [replace(x, title=query) for x in original(query, scope, **kwargs)]
    session.search = selected
    seen = []
    monkeypatch.setattr(
        service, "_mirror_remote_results",
        lambda rows, **kw: seen.extend((r.title, kw["identity_id"]) for r in rows),
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            old = pool.submit(service.search, "Old Book", force=True)
            assert entered.wait(5)
            fresh = service.search("New Book", force=True)
            release.set()
            assert old.result(timeout=5) == []
        assert fresh and all(r.title == "New Book" for r in fresh)
        assert all(r.title == "New Book" for r in service.last_search)
        assert seen and all(t == "New Book" and owner == "alice" for t, owner in seen)
    finally:
        release.set()
        service.close()


def test_sync_search_mirror_bound_to_original_identity_even_if_switches_inside(
    tmp_path, monkeypatch,
):
    service = _rig(tmp_path)
    original_mirror = service._mirror_remote_results
    captured = []
    def mirror(rows, **kwargs):
        captured.append(kwargs.get("identity_id"))
        service.select_identity("bob")
        return original_mirror(rows, **kwargs)
    monkeypatch.setattr(service, "_mirror_remote_results", mirror)
    try:
        assert service.search("Progressive Book", force=True) == []
        assert captured == ["alice"]
        assert service.last_search == []
        assert {x["identity_id"] for x in service.storage.catalog_entries()} == {"alice"}
        assert not any(
            row["namespace"] == "search"
            for row in service.storage.cache_namespace_stats()
        )
    finally:
        service.close()


def test_sync_search_cache_hit_respects_profile_generation(tmp_path, monkeypatch):
    service = _rig(tmp_path)
    try:
        original = service.search("Progressive Book", force=True)
        assert original
        service.last_search = []
        def switch_on_cached_read(*args, **kwargs):
            service.select_identity("bob")
            return [asdict(original[0])]
        monkeypatch.setattr(service.storage, "cache_get", switch_on_cached_read)
        assert service.search("Progressive Book") == []
        assert service.last_search == []
    finally:
        service.close()
