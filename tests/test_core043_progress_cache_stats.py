from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from cifereca.cache_runtime import CacheBrokerImpl
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import SearchResult
from cifereca.plugin_api import MANIFEST_VERSION, PLUGIN_API_VERSION
from cifereca.services import LibraryService
from cifereca.storage import CURRENT_SCHEMA_VERSION, Storage


def _service(tmp_path: Path) -> LibraryService:
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "library"))
    cfg.set("identities", [{"id": "alice", "label": "Alice"}])
    cfg.set("providers.accounts", [])
    return LibraryService(cfg, Storage(tmp_path / "db.sqlite3"), Diagnostics())


class _FakeSession:
    def __init__(self, provider_id: str, *, fail: bool = False):
        self.provider_id = provider_id
        self.credential_id = provider_id + "-credential"
        self.connected = True
        self.capabilities = SimpleNamespace(search=True)
        self.descriptor = SimpleNamespace(display_name=provider_id.title())
        self.client = None
        self.last_search_warnings = []
        self.fail = fail

    def search(self, query, scope, *, field="all", content_types=()):
        if self.fail:
            raise ConnectionError("provider unavailable")
        return [SearchResult(
            id=f"{self.provider_id}:1", record_id="1", docbase=self.provider_id.upper(), medium="digital",
            title="Progressive Book", author="A", type="ebook", format="EPUB", publisher=None,
            published_at=None, description=None, thumbnail_url=None, friendly_url=None,
            scenario=self.provider_id.upper(), source_provider=self.provider_id,
            source_credential=self.credential_id,
        )]


def test_core043_public_versions_and_schema():
    assert PLUGIN_API_VERSION == "1.4.2"
    assert MANIFEST_VERSION == 1
    assert CURRENT_SCHEMA_VERSION == 14


def test_progressive_search_isolates_provider_failure(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        service._sessions = {"good": _FakeSession("good"), "bad": _FakeSession("bad", fail=True)}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        batches = []
        summary = service.search_progressive("Progressive Book", force=True, on_batch=batches.append)
        assert {b.provider_id for b in batches} == {"good", "bad"}
        assert any(b.provider_id == "good" and len(b.results) == 1 for b in batches)
        assert any(b.provider_id == "bad" and b.error_code for b in batches)
        assert summary.providers_total == 2
        assert summary.providers_completed == 2
        assert summary.providers_failed == 1
        assert summary.result_count == 1
        assert service.last_search[0].source_provider == "good"
    finally:
        service.close()



class _CancelledControl:
    def cancelled(self): return True
    def check_cancelled(self): raise RuntimeError("cancelled")
    def report(self, **_kwargs): pass


def test_progressive_search_honours_preexisting_cancellation(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        service._sessions = {"good": _FakeSession("good")}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        batches = []
        summary = service.search_progressive("Progressive Book", force=True, on_batch=batches.append, control=_CancelledControl())
        assert summary.cancelled is True
        assert summary.providers_completed == 0
        assert batches == []
    finally:
        service.close()

def test_cache_broker_is_scoped_per_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    service = _service(tmp_path)
    try:
        cache_root = tmp_path / "cache-home" / "cifereca" / "plugins"
        own = cache_root / "plugin.a"; own.mkdir(parents=True); (own / "a.bin").write_bytes(b"a")
        other = cache_root / "plugin.b"; other.mkdir(parents=True); (other / "b.bin").write_bytes(b"bb")
        broker = CacheBrokerImpl(service)
        scoped = broker.for_plugin("plugin.a", trusted=False)
        assert {row.namespace_id for row in scoped.namespaces()} == {"db:plugin:plugin.a", "plugin:plugin.a"}
        denied = scoped.purge("plugin:plugin.b")
        assert not denied.ok and (other / "b.bin").exists()
        own_result = scoped.purge("plugin:plugin.a")
        assert own_result.ok and own_result.entries_removed == 1
        assert not (own / "a.bin").exists()
        trusted = broker.for_plugin("core.maintenance.cache", trusted=True)
        ids = {row.namespace_id for row in trusted.namespaces()}
        assert "plugin:plugin.b" in ids
    finally:
        service.close()


def test_cache_broker_key_value_ttl_is_scoped(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        broker = CacheBrokerImpl(service)
        first = broker.for_plugin("plugin.a", trusted=False)
        second = broker.for_plugin("plugin.b", trusted=False)

        assert first.get("missing", {"fallback": True}) == {"fallback": True}
        first.put("shared-key", {"value": 1}, ttl_seconds=60)
        second.put("shared-key", {"value": 2}, ttl_seconds=60)
        assert first.get("shared-key") == {"value": 1}
        assert second.get("shared-key") == {"value": 2}

        assert first.delete("shared-key") is True
        assert first.delete("shared-key") is False
        assert second.get("shared-key") == {"value": 2}

        import cifereca.storage as storage_module
        now = storage_module.time.time()
        first.put("short", "alive", ttl_seconds=1)
        monkeypatch.setattr(storage_module.time, "time", lambda: now + 2)
        assert first.get("short") is None

        result = second.purge("db:plugin:plugin.b")
        assert result.ok
        assert second.get("shared-key") is None
    finally:
        service.close()


def test_cache_broker_singleflight_collapses_concurrent_misses(tmp_path):
    service = _service(tmp_path)
    try:
        broker = CacheBrokerImpl(service)
        scopes = [broker.for_plugin("plugin.singleflight", trusted=False) for _ in range(8)]
        barrier = threading.Barrier(len(scopes))
        calls = 0
        calls_lock = threading.Lock()

        def producer():
            nonlocal calls
            with calls_lock:
                calls += 1
                value = calls
            time.sleep(0.08)
            return {"value": value}

        def run(scope):
            barrier.wait(timeout=2)
            return scope.get_or_compute("expensive", producer, ttl_seconds=60)

        with ThreadPoolExecutor(max_workers=len(scopes)) as pool:
            results = list(pool.map(run, scopes))

        assert calls == 1
        assert results == [{"value": 1}] * len(scopes)

        called_again = False
        def should_not_run():
            nonlocal called_again
            called_again = True
            return {"value": 2}
        assert scopes[0].get_or_compute("expensive", should_not_run, ttl_seconds=60) == {"value": 1}
        assert called_again is False

        other = broker.for_plugin("plugin.other", trusted=False)
        assert other.get_or_compute("expensive", lambda: {"value": 9}, ttl_seconds=60) == {"value": 9}
        assert scopes[0].get("expensive") == {"value": 1}
    finally:
        service.close()


def test_action_statistics_are_persisted_with_explicit_window(tmp_path):
    service = _service(tmp_path)
    try:
        service._record_action("alice", "acquisition", "success", provider_id="paris")
        service._record_action("alice", "acquisition", "failed", provider_id="paris")
        service._record_action("alice", "transformation", "success", plugin_id="transformer.test")
        stats = service.identity_statistics("alice", window_seconds=3600)["actions"]
        assert stats["window_seconds"] == 3600
        assert stats["to_timestamp"] >= stats["from_timestamp"]
        assert stats["acquisitions_succeeded"] == 1
        assert stats["acquisitions_failed"] == 1
        assert stats["transformations_succeeded"] == 1
    finally:
        service.close()
