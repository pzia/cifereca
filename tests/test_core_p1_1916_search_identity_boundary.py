"""A superseded profile search never publishes or mirrors into the new profile."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading

import httpx
import pytest

from tests.test_core043_progress_cache_stats import _service, _FakeSession
from tests.test_core_p1_1916_stale_public_fallback import _known


def setup(tmp_path):
    service = _service(tmp_path)
    service.identities.append(replace(service.current_identity, identity_id="bob", label="Bob"))
    return service


@pytest.mark.parametrize("outage", [False, True])
@pytest.mark.parametrize("return_to_alice", [False, True])
def test_profile_change_during_provider_io_discards_live_and_stale_results(
    tmp_path, outage, return_to_alice,
):
    service = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    provider = _FakeSession("good")
    original = provider.search
    def search(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        if outage:
            raise httpx.ConnectError("PRIVATE")
        return original(*args, **kwargs)
    provider.search = search
    service._sessions = {"good": provider}
    _known(service, title="Progressive Book")
    before = service.storage.catalog_entries()
    batches = []
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.search_progressive, "Progressive Book", force=True,
                                 on_batch=batches.append)
            assert entered.wait(5)
            service.select_identity("bob")
            if return_to_alice:
                service.select_identity("alice")
            sentinel = original("new profile", "all")[0]
            service.last_search = [sentinel]
            service.last_search_warnings = ["new profile state"]
            release.set()
            summary = future.result(timeout=5)
        assert summary.cancelled is True
        assert batches == []
        assert service.last_search == [sentinel]
        assert service.last_search_warnings == ["new profile state"]
        assert service.storage.catalog_entries() == before
        assert not any(row["namespace"] == "search" for row in service.storage.cache_namespace_stats())
    finally:
        release.set()
        service.close()


def test_selecting_another_profile_revokes_previous_search_snapshot(tmp_path):
    service = setup(tmp_path)
    try:
        service.last_search = _FakeSession("good").search("old", "all")
        service.last_search_warnings = ["old profile warning"]
        service.select_identity("bob")
        assert service.last_search == service.last_search_warnings == []
    finally:
        service.close()


@pytest.mark.parametrize("cached", [False, True])
def test_profile_switch_from_batch_callback_cannot_restore_old_snapshot(tmp_path, cached):
    service = setup(tmp_path)
    service._sessions = {"good": _FakeSession("good")}
    try:
        if cached:
            service.search_progressive("Progressive Book", force=True)
        before = service.storage.catalog_entries()
        calls = []
        def batch(value):
            calls.append(value)
            service.select_identity("bob")
        summary = service.search_progressive("Progressive Book", force=not cached, on_batch=batch)
        assert len(calls) == 1
        assert summary.cancelled is True
        assert service.last_search == service.last_search_warnings == []
        assert service.storage.catalog_entries() == before
    finally:
        service.close()


def test_mirror_stays_bound_to_original_profile_even_at_identity_transition(tmp_path, monkeypatch):
    service = setup(tmp_path)
    service._sessions = {"good": _FakeSession("good")}
    original = service._mirror_remote_results
    seen = []
    def mirror(rows, **kwargs):
        seen.append(kwargs.get("identity_id"))
        service.select_identity("bob")
        return original(rows, **kwargs)
    monkeypatch.setattr(service, "_mirror_remote_results", mirror)
    try:
        summary = service.search_progressive("Progressive Book", force=True)
        assert seen == ["alice"]
        assert summary.cancelled is True
        assert service.last_search == []
        assert {row["identity_id"] for row in service.storage.catalog_entries()} == {"alice"}
    finally:
        service.close()


def test_newer_query_started_by_callback_keeps_its_snapshot_and_catalogue(tmp_path):
    service = setup(tmp_path)
    provider = _FakeSession("good")
    original = provider.search
    provider.search = lambda query, scope, **kw: [
        replace(row, title=query) for row in original(query, scope, **kw)
    ]
    service._sessions = {"good": provider}
    try:
        newer = []
        def batch(_value):
            newer.append(service.search_progressive("Newer Book", force=True))
        older = service.search_progressive("Older Book", force=True, on_batch=batch)
        assert older.cancelled and not newer[0].cancelled
        assert [row.title for row in service.last_search] == ["Newer Book"]
        assert [row["title"] for row in service.storage.catalog_entries()] == ["Newer Book"]
    finally:
        service.close()


def test_already_cancelled_request_never_starts_provider_io(tmp_path):
    service = setup(tmp_path)
    provider = _FakeSession("good")
    def forbidden(*args, **kwargs):
        pytest.fail("Cancelled search must not start HTTP")
    provider.search = forbidden
    service._sessions = {"good": provider}
    try:
        from types import SimpleNamespace
        summary = service.search_progressive(
            "Progressive Book", force=True,
            control=SimpleNamespace(cancelled=lambda: True),
        )
        assert summary.cancelled and summary.providers_completed == 0
        assert service.last_search == []
    finally:
        service.close()
