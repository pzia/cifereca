from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx

from cifereca.gui.cover_loader import CoverLoader
from cifereca.epub_metadata import MAX_IMAGE


# Signature PNG valide pour la validation légère du cache (le loader ne décode pas l'image).
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BODY = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"


def test_cover_loader_returns_valid_disk_cache_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader("https://bibliotheques.paris.fr", max_workers=2)
    try:
        url = "https://covers.example.test/book.png"
        path = loader._path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG_HEAD)
        seen: list[Path | None] = []
        request = loader.load(url, seen.append)
        assert seen == [path]
        assert loader.stats()["cache_hit_valid"] == 1
        request.cancel()
    finally:
        loader.close()


def test_cover_loader_invalid_cache_is_redownloaded(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader("https://bibliotheques.paris.fr", max_workers=2)
    old = loader.client
    try:
        url = "https://covers.example.test/book.jpg"
        path = loader._path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"<html>not an image</html>")

        def handler(request: httpx.Request):
            return httpx.Response(200, content=JPEG_BODY, headers={"content-type": "image/jpeg"})

        loader.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        old.close()
        done = threading.Event()
        seen: list[Path | None] = []

        def callback(value):
            seen.append(value)
            done.set()

        loader.load(url, callback)
        assert done.wait(2.0)
        assert seen == [path]
        stats = loader.stats()
        assert stats["cache_invalid"] == 1
        assert stats["download_ok"] == 1
        assert path.read_bytes().startswith(b"\xff\xd8\xff")
    finally:
        loader.close()


def test_cover_loader_tracks_ui_decode_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader("https://bibliotheques.paris.fr", max_workers=2)
    try:
        loader.note_ui_decode(12.5)
        loader.note_ui_decode(42.0)
        stats = loader.stats()
        assert stats["ui_decode_count"] == 2
        assert stats["ui_decode_avg_ms"] == 27.25
        assert stats["ui_decode_max_ms"] == 42.0
        assert stats["ui_decode_slow"] == 1
    finally:
        loader.close()



def test_oversized_remote_cover_is_not_cached_or_delivered(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader(max_workers=2)
    original = loader.client
    url = "https://covers.example.test/oversized.png"
    oversized = PNG_HEAD + b"X" * (MAX_IMAGE - len(PNG_HEAD) + 1)
    called = []

    def handler(request):
        called.append(request.url)
        return httpx.Response(200, content=oversized,
                              headers={"content-type": "image/png"})

    loader.client = httpx.Client(transport=httpx.MockTransport(handler))
    original.close()
    done = threading.Event()
    seen = []
    try:
        loader.load(url, lambda value: (seen.append(value), done.set()))
        assert done.wait(5.0)
        assert len(called) == 1
        assert seen == [None]
        assert not loader._path(url).exists()
        assert loader.stats()["download_failed"] == 1
        assert loader.stats()["download_ok"] == 0
    finally:
        loader.close()


def test_oversized_old_cover_cache_is_replaced_with_bounded_valid_image(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader(max_workers=2)
    url = "https://covers.example.test/recovered.png"
    path = loader._path(url)
    path.write_bytes(PNG_HEAD + b"X" * (MAX_IMAGE - len(PNG_HEAD) + 1))
    calls = []
    original = loader.client

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, content=PNG_HEAD,
                              headers={"content-type": "image/png"})

    loader.client = httpx.Client(transport=httpx.MockTransport(handler))
    original.close()
    seen = []
    done = threading.Event()
    try:
        loader.load(url, lambda value: (seen.append(value), done.set()))
        assert done.wait(5.0)
        assert len(calls) == 1
        assert seen == [path]
        assert path.read_bytes() == PNG_HEAD
        assert loader.stats()["cache_invalid"] == 1
        assert loader.stats()["download_ok"] == 1
        cached = []
        loader.load(url, cached.append)
        assert cached == [path]
        assert len(calls) == 1
    finally:
        loader.close()


def test_cover_cooldown_fallback_can_reenter_loader_without_deadlocking(
    tmp_path, monkeypatch,
):
    """A cached HTTP failure must never call UI/preview callbacks under _lock."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader(max_workers=2)
    url = "https://covers.example.test/cooldown.png"
    # No request is enqueued: simulate a prior bounded HTTP failure.
    with loader._lock:
        loader._fail_until[url] = time.monotonic() + 60.0
    seen = []
    try:
        def first(value):
            assert value is None
            assert not loader._lock.locked()
            assert loader.stats()["requested"] == 1
            seen.append("first")
            # A detail/list callback may immediately retry a different
            # binding or request another placeholder from the same loader.
            loader.load(url, lambda nested: seen.append(("nested", nested)))

        loader.load(url, first)
        assert seen == ["first", ("nested", None)]
        assert loader.stats()["requested"] == 2
        assert loader.stats()["queue"] == 0
        assert loader.stats()["inflight_urls"] == 0

        preview = []
        loader.load_preview(url, preview.append, width=60, height=90)
        assert preview == [None]
        assert loader.stats()["queue"] == 0
    finally:
        loader.close()
