"""#6168: physical N3 EPUB diagnostics are shared and never owner-scoped."""
from __future__ import annotations

from dataclasses import replace
import hashlib
from threading import Event

import pytest

from cifereca import epub_metadata
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.epub_cover_materialization import (
    EpubCoverPhysicalCache, ExactEpubCoverResource,
)
from cifereca.gui.cover_loader import CoverLoader
from tests.test_epub_cover_contract import _write_epub


@pytest.fixture
def physical(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = tmp_path / "exact.epub"
    raw = _write_epub(path, epub3=True)
    metadata = epub_metadata.inspect_epub(path)
    digest = hashlib.sha256(raw).hexdigest()
    scope = CoverDocumentScope(
        kind="placement", identity_id="alice",
        source_key="artifact:sha256:" + digest,
        artifact_id="sha256:" + digest,
        placement_id="place:a",
    )
    resource = ExactEpubCoverResource(
        scope=scope, source_revision=1, artifact_sha256=digest,
        member=metadata["epub_cover"]["member"],
        cache_name=metadata["epub_cover"]["cache_name"],
    )
    image = epub_metadata.cover_cache_dir() / resource.cache_name
    assert epub_metadata.valid_cached_cover_path(image)
    return path, resource, image


def test_hot_n3_hits_shared_across_profiles_and_exposed_by_loader(physical):
    path, original, image = physical
    other = replace(
        original, source_revision=99,
        scope=replace(original.scope, identity_id="bob",
                      placement_id="place:b"),
    )
    loader = CoverLoader(max_workers=2)
    try:
        one = loader._epub_physical.request(original, source_path=path)
        two = loader._epub_physical.request(other, source_path=path)
        assert one.done() and two.done()
        assert one.result() == two.result() == image.resolve().as_uri()
        stats = loader.stats()
        assert stats["n3_epub_hit"] == 2
        assert stats["n3_epub_miss"] == 0
        assert stats["n3_epub_singleflight"] == 0
        assert stats["n3_epub_ok_recent_count"] == 0
        assert stats["n3_epub_failed_recent_count"] == 0
        assert "n3_epub_ok_recent_p95_ms" not in stats
        assert "alice" not in repr(stats) and "bob" not in repr(stats)
        assert "place:" not in repr(stats)
    finally:
        loader.close()


def test_cold_n3_reuses_one_epub_worker_for_two_exact_documents(
    physical, monkeypatch,
):
    path, original, image = physical
    other = replace(
        original, source_revision=99,
        scope=replace(original.scope, identity_id="bob",
                      placement_id="place:b"),
    )
    image.unlink()
    cache = EpubCoverPhysicalCache(max_workers=2)
    entered, release = Event(), Event()
    calls = []
    actual_restore = cache._restore

    def gated(source, source_path):
        calls.append((source, source_path))
        entered.set()
        assert release.wait(10)
        return actual_restore(source, source_path)

    monkeypatch.setattr(cache, "_restore", gated)
    try:
        first = cache.request(original, source_path=path)
        assert entered.wait(10)
        second = cache.request(other, source_path=path)
        assert first is second
        stats = cache.stats()
        assert stats["n3_epub_hit"] == 0
        assert stats["n3_epub_miss"] == 1
        assert stats["n3_epub_singleflight"] == 1
        assert stats["inflight"] == 1
        release.set()
        assert first.result(timeout=10) == image.resolve().as_uri()
        assert len(calls) == 1
        again = cache.request(other, source_path=path)
        assert again.done() and again.result() == image.resolve().as_uri()
        assert cache.stats()["n3_epub_hit"] == 1
        stats = cache.stats()
        assert stats["n3_epub_ok_recent_count"] == 1
        assert stats["n3_epub_failed_recent_count"] == 0
        assert stats["n3_epub_ok_recent_p95_ms"] >= 0
    finally:
        release.set()
        cache.close()


def test_failed_epub_is_one_miss_followed_by_cooldown_not_new_documentary_absence(
    physical, monkeypatch,
):
    path, source, image = physical
    image.unlink()
    clock = [20.0]
    cache = EpubCoverPhysicalCache(
        negative_ttl=30.0, clock=lambda: clock[0],
    )
    done = Event()
    original_release = cache._release

    def observe(key, finished):
        original_release(key, finished)
        done.set()

    monkeypatch.setattr(cache, "_release", observe)
    monkeypatch.setattr(cache, "_restore", lambda *_: None)
    try:
        assert cache.request(source, source_path=path).result(timeout=10) is None
        assert done.wait(10)
        assert cache.stats()["n3_epub_failed"] == 1
        assert cache.stats()["n3_epub_miss"] == 1
        assert cache.request(source, source_path=path).result() is None
        assert cache.stats()["n3_epub_cooldown"] == 1
        assert cache.stats()["n3_epub_failed_recent_count"] == 1
        assert cache.stats()["n3_epub_miss"] == 1
        clock[0] = 50.1
        done.clear()
        assert cache.request(source, source_path=path).result(timeout=10) is None
        assert done.wait(10)
        assert cache.stats()["n3_epub_miss"] == 2
        assert cache.stats()["n3_epub_failed"] == 2
        assert cache.stats()["n3_epub_failed_recent_count"] == 2
        assert cache.stats()["n3_epub_ok_recent_count"] == 0
    finally:
        cache.close()


def test_distinct_source_overflow_not_double_counted_as_n3_miss(
    physical, monkeypatch,
):
    path, source, image = physical
    image.unlink()
    another_path = path.with_name("other.epub")
    cache = EpubCoverPhysicalCache(max_workers=1, max_inflight=1)
    entered, release = Event(), Event()
    actual_restore = cache._restore

    def gated(resource, source_path):
        entered.set()
        assert release.wait(10)
        return actual_restore(resource, source_path)

    monkeypatch.setattr(cache, "_restore", gated)
    try:
        first = cache.request(source, source_path=path)
        assert entered.wait(10)
        # Same Artifact but a distinct source file is a distinct physical
        # task; no extra slot can be admitted until the first is finished.
        assert cache.request(
            source, source_path=another_path,
        ).result() is None
        assert cache.stats()["n3_epub_miss"] == 1
        assert cache.stats()["n3_epub_overflow"] == 1
        release.set()
        assert first.result(timeout=10) == image.resolve().as_uri()
    finally:
        release.set()
        cache.close()


def test_epub_worker_percentiles_are_bounded_and_separate():
    cache = EpubCoverPhysicalCache()
    try:
        for value in range(1025):
            cache._record_worker_latency("ok", float(value))
        cache._record_worker_latency("failed", 7.0)
        cache._record_worker_latency("failed", float("nan"))
        cache._record_worker_latency("failed", -1.0)
        stats = cache.stats()
        assert stats["n3_epub_ok_recent_count"] == 1024
        assert stats["n3_epub_ok_recent_p50_ms"] == 512
        assert stats["n3_epub_ok_recent_p95_ms"] == 973
        assert stats["n3_epub_failed_recent_count"] == 1
        assert stats["n3_epub_failed_recent_p95_ms"] == 7
    finally:
        cache.close()


def test_n3_rejects_fifo_without_blocking_worker(physical, monkeypatch):
    import os
    from cifereca.core import epub_cover_materialization as materialization
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("requires POSIX FIFO")
    path, source, image = physical
    image.unlink()
    path.unlink()
    os.mkfifo(path)
    actual_open = os.open

    def checked_open(name, flags, *args, **kwargs):
        if name == path:
            # Fail the test before opening a FIFO if the guard regresses.
            assert flags & os.O_NONBLOCK
        return actual_open(name, flags, *args, **kwargs)

    opened = []
    def observe_open(name, flags, *args, **kwargs):
        if name == path:
            opened.append(flags)
        return checked_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(materialization.os, "open", observe_open)
    assert EpubCoverPhysicalCache._restore(source, path) is None
    assert len(opened) == 1 and opened[0] & os.O_NONBLOCK
    assert not image.exists()


def test_n3_refuses_growth_between_archive_read_and_publication(physical, monkeypatch):
    from cifereca.core import epub_cover_materialization as materialization
    path, source, image = physical
    image.unlink()
    actual_read = materialization._read
    calls = []

    def grow(archive, member, limit):
        value = actual_read(archive, member, limit)
        with path.open("ab") as output:
            output.write(b"changed")
        calls.append(member)
        return value

    monkeypatch.setattr(materialization, "_read", grow)
    assert EpubCoverPhysicalCache._restore(source, path) is None
    assert calls == [source.member]
    assert not image.exists()


def test_cancelled_queued_n3_is_not_a_failure_or_negative_cache(physical, monkeypatch):
    path, source, image = physical
    other = path.with_name("second.epub")
    other.write_bytes(path.read_bytes())
    image.unlink()
    cache = EpubCoverPhysicalCache(max_workers=1, max_inflight=2, negative_ttl=120)
    entered, release = Event(), Event()
    original_restore = cache._restore
    calls = []

    def gated(resource, source_path):
        calls.append(source_path)
        if source_path == path:
            entered.set()
            assert release.wait(10)
            # Do not create a warm cache hit that could conceal cooldown.
            return None
        return original_restore(resource, source_path)

    monkeypatch.setattr(cache, "_restore", gated)
    try:
        busy = cache.request(source, source_path=path)
        assert entered.wait(10)
        queued = cache.request(source, source_path=other)
        assert queued.cancel()
        stats = cache.stats()
        assert stats["inflight"] == 1
        assert stats["n3_epub_failed"] == stats["n3_epub_cooldown"] == 0
        assert stats["n3_epub_failed_recent_count"] == 0
        again = cache.request(source, source_path=other)
        assert again is not queued and not again.done()
        release.set()
        assert busy.result(timeout=10) is None
        assert again.result(timeout=10) == image.resolve().as_uri()
        assert calls == [path, other]
        assert cache.stats()["n3_epub_cooldown"] == 0
        assert cache.stats()["n3_epub_ok_recent_count"] == 1
    finally:
        release.set()
        cache.close()
