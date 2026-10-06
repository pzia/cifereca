from __future__ import annotations

import threading
from pathlib import Path

import httpx

from cifereca.gui.cover_loader import CoverLoader


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
