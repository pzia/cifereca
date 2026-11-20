"""P1 #3104: queued thumbnail conversion is canceled only when no GTK cell needs it.

Exercise the real Core preview scheduling/dedup callbacks with a manually
controlled executor. No GTK, provider HTTP, image decoding or race sleeps.
"""
from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui import cover_loader as mod


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class ManualPool:
    def __init__(self):
        self.jobs = []

    def submit(self, task):
        future = Future()
        self.jobs.append((task, future))
        return future

    def run(self, index):
        task, future = self.jobs[index]
        future.set_result(task())

    def shutdown(self, **_kwargs):
        pass


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = mod.CoverLoader(max_workers=2)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    actual_pool = loader._preview_pool
    actual_pool.shutdown(wait=True)
    pool = ManualPool()
    loader._preview_pool = pool
    conversions = []
    monkeypatch.setattr(
        loader, "load",
        lambda _url, callback, **_kwargs:
            (callback(source), NS(cancel=lambda: None))[1],
    )
    monkeypatch.setattr(
        mod, "preview_path",
        lambda _source, _root, width, height: tmp_path / f"{width}x{height}.png",
    )

    def create(_source, dest, width, height):
        conversions.append((width, height))
        dest.write_bytes(PNG)

    monkeypatch.setattr(mod, "create_preview", create)
    return loader, pool, conversions


def test_queued_preview_without_any_live_consumer_skips_conversion(tmp_path, monkeypatch):
    loader, pool, conversions = setup(tmp_path, monkeypatch)
    try:
        received = []
        first = loader.load_preview("book", received.append, width=60, height=90)
        second = loader.load_preview("book", received.append, width=60, height=90)
        assert len(pool.jobs) == 1
        assert len(loader._preview_waiters) == 1
        first.cancel()
        second.cancel()
        pool.run(0)
        assert conversions == [] and received == []
        assert loader._preview_jobs == {}
        assert loader._preview_waiters == {}

        # The discarded task cannot starve a new, live request for the key.
        result = []
        loader.load_preview("book", result.append, width=60, height=90)
        assert len(pool.jobs) == 2
        pool.run(1)
        assert conversions == [(60, 90)]
        assert result == [tmp_path / "60x90.png"]
    finally:
        loader.close()


def test_shared_preview_remains_eligible_when_one_consumer_cancels(tmp_path, monkeypatch):
    loader, pool, conversions = setup(tmp_path, monkeypatch)
    try:
        discarded, received = [], []
        first = loader.load_preview("book", discarded.append, width=80, height=120)
        loader.load_preview("book", received.append, width=80, height=120)
        first.cancel()
        pool.run(0)
        assert conversions == [(80, 120)]
        assert discarded == []
        assert received == [tmp_path / "80x120.png"]
        assert loader._preview_jobs == {}
        assert loader._preview_waiters == {}
    finally:
        loader.close()


def test_preview_worker_metrics_are_per_conversion_not_subscriber(tmp_path, monkeypatch):
    loader, pool, conversions = setup(tmp_path, monkeypatch)
    clock = [10.0]
    monkeypatch.setattr(mod.time, "perf_counter", lambda: clock[0])
    original_create = mod.create_preview

    def timed(*args):
        clock[0] += .004
        return original_create(*args)

    monkeypatch.setattr(mod, "create_preview", timed)
    try:
        first = loader.load_preview("book", lambda _: None, width=60, height=90)
        loader.load_preview("book", lambda _: None, width=60, height=90)
        clock[0] += 100.0  # queued time not included
        first.cancel()
        pool.run(0)
        stats = loader.stats()
        assert stats["preview_created"] == 1
        assert stats["preview_worker_created_recent_count"] == 1
        assert stats["preview_worker_created_recent_p95_ms"] == 4
        loader.load_preview("book", lambda _: None, width=60, height=90)
        pool.run(1)
        assert loader.stats()["preview_worker_hit_recent_count"] == 1
        canceled = loader.load_preview("book", lambda _: None, width=70, height=90)
        canceled.cancel()
        pool.run(2)
        assert loader.stats()["preview_worker_created_recent_count"] == 1

        def fail(*args):
            clock[0] += .007
            raise ValueError("synthetic preview failure")

        monkeypatch.setattr(mod, "create_preview", fail)
        loader.load_preview("book", lambda _: None, width=80, height=90)
        pool.run(3)
        stats = loader.stats()
        assert stats["preview_worker_fallback_recent_count"] == 1
        assert stats["preview_worker_fallback_recent_p95_ms"] == 7
        assert stats["preview_fallback"] == 1
        assert stats["preview_worker_hit_recent_p95_ms"] == 0
        assert len(conversions) == 1
    finally:
        loader.close()


def test_preview_worker_recent_samples_bounded(tmp_path, monkeypatch):
    loader, _, _ = setup(tmp_path, monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(mod.time, "perf_counter", lambda: clock[0])
    try:
        for n in range(1025):
            clock[0] = n / 1000
            loader._record_preview_worker("created", 0.0)
        stats = loader.stats()
        assert stats["preview_created"] == 1025
        assert stats["preview_worker_created_recent_count"] == 1024
        assert stats["preview_worker_created_recent_p50_ms"] == 512
        assert stats["preview_worker_created_recent_p95_ms"] == 973
        assert stats["preview_worker_hit_recent_count"] == 0
        assert "preview_worker_hit_recent_p95_ms" not in stats
    finally:
        loader.close()
