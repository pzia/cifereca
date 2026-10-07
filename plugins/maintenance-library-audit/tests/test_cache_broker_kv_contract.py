from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import time

from cifereca.cache_runtime import CacheBrokerImpl
from cifereca.plugin_api import CacheBroker
from cifereca.storage import Storage


def test_cache_broker_public_kv_ttl_contract(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "cache.sqlite3")
    try:
        broker = CacheBrokerImpl(SimpleNamespace(storage=storage))
        scoped = broker.for_plugin("maintenance.audit.test", trusted=False)
        other = broker.for_plugin("maintenance.audit.other", trusted=False)

        assert isinstance(scoped, CacheBroker)
        scoped.put("plan", {"count": 3}, ttl_seconds=60)
        other.put("plan", {"count": 9}, ttl_seconds=60)
        assert scoped.get("plan") == {"count": 3}
        assert other.get("plan") == {"count": 9}
        assert {row.namespace_id for row in scoped.namespaces()} == {
            "db:plugin:maintenance.audit.test",
            "plugin:maintenance.audit.test",
        }

        denied = scoped.purge("db:plugin:maintenance.audit.other")
        assert not denied.ok
        assert other.get("plan") == {"count": 9}

        import cifereca.storage as storage_module
        now = storage_module.time.time()
        scoped.put("short", "value", ttl_seconds=1)
        monkeypatch.setattr(storage_module.time, "time", lambda: now + 2)
        assert scoped.get("short") is None
    finally:
        storage.close()



def test_cache_broker_singleflight_is_shared_per_plugin(tmp_path):
    storage = Storage(tmp_path / "singleflight.sqlite3")
    try:
        broker = CacheBrokerImpl(SimpleNamespace(storage=storage))
        scopes = [broker.for_plugin("maintenance.audit.test", trusted=False) for _ in range(6)]
        barrier = threading.Barrier(len(scopes))
        calls = 0
        lock = threading.Lock()

        def producer():
            nonlocal calls
            with lock:
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
        assert scopes[0].get("expensive") == {"value": 1}

        other = broker.for_plugin("maintenance.audit.other", trusted=False)
        assert other.get_or_compute("expensive", lambda: {"value": 2}, ttl_seconds=60) == {"value": 2}
        assert scopes[0].get("expensive") == {"value": 1}
    finally:
        storage.close()
