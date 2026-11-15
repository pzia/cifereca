"""#6168 N3 work is physical-resource scoped, not documentary N2 scoped."""
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
from tests.test_epub_cover_contract import _write_epub


@pytest.fixture
def physical(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = tmp_path / "exact.epub"
    raw = _write_epub(path, epub3=True)
    meta = epub_metadata.inspect_epub(path)
    scope = CoverDocumentScope(
        kind="placement", identity_id="alice", source_key="doc:a",
        artifact_id="sha256:" + hashlib.sha256(raw).hexdigest(),
        placement_id="placement:a",
    )
    ref = ExactEpubCoverResource(
        scope=scope, source_revision=1,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        member=meta["epub_cover"]["member"],
        cache_name=meta["epub_cover"]["cache_name"],
    )
    image = epub_metadata.cover_cache_dir() / ref.cache_name
    assert epub_metadata.valid_cached_cover_path(image)
    return path, ref, image


def test_same_n3_source_shared_across_different_documentrefs_and_revisions(
    physical, monkeypatch,
):
    path, first, image = physical
    second = replace(
        first, scope=replace(first.scope, identity_id="bob",
                             source_key="doc:b", placement_id="placement:b"),
        source_revision=99,
    )
    assert first.scope != second.scope
    image.unlink()
    cache = EpubCoverPhysicalCache(max_workers=2)
    started, release = Event(), Event()
    calls = []
    restore = cache._restore

    def gated(ref, source_path):
        calls.append((ref, source_path))
        started.set()
        assert release.wait(10)
        return restore(ref, source_path)

    monkeypatch.setattr(cache, "_restore", gated)
    try:
        one = cache.request(first, source_path=path)
        assert started.wait(10)
        two = cache.request(second, source_path=path)
        assert one is two
        assert cache.stats()["inflight"] == 1
        release.set()
        assert one.result(timeout=10) == image.resolve().as_uri()
        assert len(calls) == 1
        assert epub_metadata.valid_cached_cover_path(image)
    finally:
        release.set()
        cache.close()


def test_failed_epub_is_not_rehashed_on_every_cell_rebind(physical, monkeypatch):
    path, ref, image = physical
    image.unlink()
    now = [0.0]
    cache = EpubCoverPhysicalCache(
        negative_ttl=20.0, clock=lambda: now[0],
    )
    calls = []
    finished = Event()
    release = cache._release

    def observe(key, future):
        release(key, future)
        finished.set()

    def broken(resource, source_path):
        calls.append((resource, source_path))
        return None

    monkeypatch.setattr(cache, "_release", observe)
    monkeypatch.setattr(cache, "_restore", broken)
    try:
        assert cache.request(ref, source_path=path).result(timeout=10) is None
        assert finished.wait(10)
        for _ in range(5):
            assert cache.request(ref, source_path=path).result(timeout=10) is None
        assert len(calls) == 1  # no repeated ZIP/SHA scan on cold-failure hit
        now[0] = 20.1
        finished.clear()
        assert cache.request(ref, source_path=path).result(timeout=10) is None
        assert finished.wait(10)
        assert len(calls) == 2
    finally:
        cache.close()


def test_valid_image_published_elsewhere_overrides_failed_source_cooldown(
    physical, monkeypatch,
):
    path, ref, image = physical
    original = image.read_bytes()
    image.unlink()
    now = [0.0]
    cache = EpubCoverPhysicalCache(
        negative_ttl=120.0, clock=lambda: now[0],
    )
    done = Event()
    release = cache._release

    def observe(key, future):
        release(key, future)
        done.set()

    monkeypatch.setattr(cache, "_release", observe)
    monkeypatch.setattr(cache, "_restore", lambda *args: None)
    try:
        assert cache.request(ref, source_path=path).result(timeout=10) is None
        assert done.wait(10)
        image.write_bytes(original)  # independently restored exact N3 bytes
        result = cache.request(ref, source_path=path)
        assert result.done()
        assert result.result(timeout=10) == image.resolve().as_uri()
        # This is a physical cache HIT; no N2 re-resolution is needed.
    finally:
        cache.close()


def test_negative_cache_does_not_suppress_independent_physical_source(
    physical, monkeypatch,
):
    path, first, image = physical
    image.unlink()
    other_path = path.with_name("second.epub")
    other_path.write_bytes(path.read_bytes())
    cache = EpubCoverPhysicalCache(negative_ttl=30.0)
    finished = Event()
    release = cache._release
    calls = []

    def observe(key, future):
        release(key, future)
        finished.set()

    def restore(ref, source):
        calls.append(source)
        if source == path:
            return None
        return EpubCoverPhysicalCache._restore(ref, source)

    monkeypatch.setattr(cache, "_release", observe)
    monkeypatch.setattr(cache, "_restore", restore)
    try:
        assert cache.request(first, source_path=path).result(timeout=10) is None
        assert finished.wait(10)
        assert cache.request(first, source_path=other_path).result(timeout=10) == image.resolve().as_uri()
        assert calls == [path, other_path]
    finally:
        cache.close()


@pytest.mark.parametrize("bad", [
    {"negative_ttl": 0}, {"negative_ttl": -1},
    {"negative_ttl": True}, {"negative_ttl": 301},
    {"clock": None}, {"clock": 7},
])
def test_invalid_cooldown_configuration_is_rejected(bad):
    with pytest.raises(ValueError):
        EpubCoverPhysicalCache(**bad)
