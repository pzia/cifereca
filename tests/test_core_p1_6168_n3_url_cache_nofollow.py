"""#6168 N3: a URL cache hit is physical bytes, not an arbitrary local symlink."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from threading import Event

import httpx

import pytest

from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.cover_preview import preview_path


URL = "https://public.example.org/book-cover.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


@pytest.fixture
def loader(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = CoverLoader(max_workers=1)
    try:
        yield source
    finally:
        source.close()


def test_regular_physical_url_cache_hit_is_still_shared(loader):
    image = loader._path(URL)
    image.write_bytes(PNG)
    assert loader._valid_cache_file(image)
    state = loader.probe_media(URL)
    assert state.source_kind == "remote" and state.source_cached
    assert not state.preview_cached
    result = []
    loader.load(URL, result.append)
    assert result == [image]
    assert loader.stats()["download_ok"] == 0


def test_symlinked_url_cache_cannot_borrow_another_local_image(
    loader, tmp_path,
):
    foreign = tmp_path / "private-image.png"
    foreign.write_bytes(PNG)
    cached = loader._path(URL)
    cached.symlink_to(foreign)
    assert not loader._valid_cache_file(cached)
    state = loader.probe_media(URL)
    assert state.source_kind == "remote"
    assert not state.source_cached and not state.preview_cached
    assert foreign.read_bytes() == PNG  # No mutation of unrelated local file.


def test_symlinked_preview_is_not_accepted_as_valid_n3_cache(
    loader, tmp_path,
):
    cached = loader._path(URL)
    cached.write_bytes(PNG)
    preview = preview_path(cached, loader.preview_root, 180, 270)
    foreign = tmp_path / "other-profile-private-preview.png"
    foreign.write_bytes(PNG)
    preview.symlink_to(foreign)
    state = loader.probe_media(URL, width=180, height=270)
    assert state.source_cached
    assert not state.preview_cached


def test_cache_file_must_be_regular_and_bounded(loader, tmp_path):
    path = loader._path(URL)
    assert not loader._valid_cache_file(path)
    path.write_bytes(PNG)
    assert loader._valid_cache_file(path)
    path.write_bytes(b"<html>not an image</html>")
    assert not loader._valid_cache_file(path)
    path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    assert not loader._valid_cache_file(path)
    if hasattr(os, "mkfifo"):
        path.unlink()
        os.mkfifo(path)
        # O_NONBLOCK prevents reading an attacker-controlled pipe from
        # hanging a GUI thread before the regular-file fstat gate.
        assert not loader._valid_cache_file(path)


def test_n3_url_cache_key_not_split_by_profile(loader):
    a = loader._path(URL)
    b = loader._path(URL)
    assert a == b
    assert a.name.endswith(".img")
    assert len(a.stem) == 64


def test_data_image_publication_does_not_write_through_fixed_tmp_symlink(
    loader, tmp_path,
):
    """An external file must survive a preplanted historical .tmp target."""
    foreign = tmp_path / "private-outside-cache"
    foreign.write_bytes(b"KEEP_PRIVATE_BYTES")
    uri = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    cache = loader._path(uri)
    trap = cache.with_suffix(".tmp")
    trap.symlink_to(foreign)
    received = []
    loader.load(uri, received.append)
    assert received == [cache]
    assert loader._valid_cache_file(cache)
    assert cache.read_bytes() == PNG
    assert trap.is_symlink()
    assert foreign.read_bytes() == b"KEEP_PRIVATE_BYTES"


def test_http_image_publication_does_not_write_through_fixed_tmp_symlink(
    loader, tmp_path,
):
    """The worker takes the same exclusive atomic publication route."""
    foreign = tmp_path / "other-profile-private-content"
    foreign.write_bytes(b"DO_NOT_TOUCH")
    cache = loader._path(URL)
    trap = cache.with_suffix(".tmp")
    trap.symlink_to(foreign)

    original = loader.client
    loader.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "image/png"},
                content=PNG, request=request,
            )
        )
    )
    original.close()
    completed = Event()
    received = []

    def callback(path):
        received.append(path)
        completed.set()

    loader.load(URL, callback)
    assert completed.wait(8), "N3 HTTP worker did not publish"
    assert received == [cache]
    assert loader._valid_cache_file(cache)
    assert cache.read_bytes() == PNG
    assert trap.is_symlink()
    assert foreign.read_bytes() == b"DO_NOT_TOUCH"
