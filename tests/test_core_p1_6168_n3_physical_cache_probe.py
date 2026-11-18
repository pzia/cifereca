"""#6168 N3: physical cover cache and preview are NOT documentary evidence."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib

import pytest

from cifereca.epub_metadata import cover_cache_dir
from cifereca.gui.cover_loader import CoverLoader, CoverMediaState
from cifereca.gui.cover_preview import preview_path

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
EMBEDDED_PNG = (b"\x89PNG\r\n\x1a\n" + b"\0" * 8
                + (2).to_bytes(4, "big") + (3).to_bytes(4, "big")
                + b"embedded-cover")
URL = "https://covers.example.org/edition.png"


@pytest.fixture
def loader(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    instance = CoverLoader(max_workers=2)
    try:
        yield instance
    finally:
        instance.close()


def test_remote_source_and_hidpi_preview_cache_are_independent(loader):
    uncached = loader.probe_media(URL, width=180, height=270)
    assert uncached == CoverMediaState("remote", False, False, 180, 270)
    source = loader._path(URL)
    source.write_bytes(PNG)
    cached_source = loader.probe_media(URL, width=180, height=270)
    assert cached_source.source_cached is True
    assert cached_source.preview_cached is False
    preview = preview_path(source, loader.preview_root, 180, 270)
    preview.write_bytes(PNG)
    cached_preview = loader.probe_media(URL, width=180, height=270)
    assert cached_preview.source_cached and cached_preview.preview_cached
    assert loader.probe_media(URL, width=120, height=180).preview_cached is False
    assert cached_preview.width == 180 and cached_preview.height == 270
    with pytest.raises(FrozenInstanceError):
        cached_preview.source_cached = False


def test_invalid_preview_and_source_are_never_reported_as_cached(loader):
    source = loader._path(URL)
    source.write_bytes(PNG)
    preview = preview_path(source, loader.preview_root, 180, 270)
    preview.write_bytes(b"<html>not image</html>")
    result = loader.probe_media(URL, width=180, height=270)
    assert result.source_cached is True and result.preview_cached is False
    source.write_bytes(b"<html>not image</html>")
    result = loader.probe_media(URL, width=180, height=270)
    assert result.source_cached is False and result.preview_cached is False


def test_probe_never_downloads_regardless_of_hit_or_miss(loader):
    class NoTransport:
        def __getattr__(self, attr):
            raise AssertionError("physical cache probe must not access transport")

    old = loader.client
    loader.client = NoTransport()
    try:
        assert loader.probe_media(URL).source_cached is False
        source = loader._path(URL)
        source.write_bytes(PNG)
        assert loader.probe_media(URL).source_cached is True
        assert loader.stats()["requested"] == 0
        assert loader.stats()["download_ok"] == 0
        assert loader.stats()["download_failed"] == 0
    finally:
        loader.client = old


def test_embedded_cover_only_from_core_epub_cache_directory(loader):
    digest = hashlib.sha256(EMBEDDED_PNG).hexdigest()
    local = cover_cache_dir() / (digest + ".png")
    local.write_bytes(EMBEDDED_PNG)
    uri = local.as_uri()
    physical = loader.probe_media(uri)
    assert physical.source_kind == "embedded"
    assert physical.source_cached is True
    assert physical.preview_cached is False
    unrelated = local.parent.parent / "foreign.png"
    unrelated.write_bytes(PNG)
    blocked = loader.probe_media(unrelated.as_uri())
    assert blocked.source_kind == "embedded"
    assert not blocked.source_cached
    assert not loader.probe_media(uri + "?token=SECRET").source_cached


def test_unsupported_scheme_is_neither_cache_hit_nor_network_request(loader):
    assert loader.probe_media("javascript:alert(1)").source_kind == "unsupported"
    assert loader.probe_media("javascript:alert(1)").source_cached is False
    assert loader.probe_media(None).source_kind == "none"
    assert loader.probe_media("").source_cached is False


@pytest.mark.parametrize("width,height", [
    (0, 90), (60, 0), (-1, 90), (2049, 90),
    (90, 2049), (True, 100), (100, False), ("60", 90),
    (60, None), (None, 90),
])
def test_dimensions_fail_closed(width, height, loader):
    with pytest.raises(ValueError):
        loader.probe_media(URL, width=width, height=height)


def test_probe_has_no_book_identity_or_url_in_exported_snapshot(loader):
    personal = "https://covers.example.org/edition.png?size=medium&t=1800000000"
    physical = loader.probe_media(personal, width=60, height=90)
    assert set(physical.__dataclass_fields__) == {
        "source_kind", "source_cached", "preview_cached", "width", "height",
    }
    assert personal not in repr(physical)
    assert physical.source_kind == "remote"


def test_probe_validates_source_freshness_for_current_preview_key(loader):
    source = loader._path(URL)
    source.write_bytes(PNG)
    preview = preview_path(source, loader.preview_root, 60, 90)
    preview.write_bytes(PNG)
    assert loader.probe_media(URL, width=60, height=90).preview_cached
    # An atomic cache replacement creates a different source mtime and may
    # change its byte length; the old preview must never count as a hit.
    newer = PNG + b"more bytes"
    temp = source.with_suffix(".new")
    temp.write_bytes(newer)
    temp.replace(source)
    next_probe = loader.probe_media(URL, width=60, height=90)
    assert next_probe.source_cached
    assert not next_probe.preview_cached


def test_same_url_serves_list_and_detail_one_valid_source_cache(loader):
    source = loader._path(URL)
    source.write_bytes(PNG)
    list_state = loader.probe_media(URL, width=180, height=270)
    detail_state = loader.probe_media(URL)
    assert list_state.source_cached and detail_state.source_cached
    assert not list_state.preview_cached
    assert detail_state.preview_cached is False
    assert loader.stats()["download_ok"] == 0


def test_embedded_image_with_same_signature_but_wrong_digest_is_n3_miss(loader):
    from cifereca.epub_metadata import cached_cover_url

    name = hashlib.sha256(EMBEDDED_PNG).hexdigest() + ".png"
    source = cover_cache_dir() / name
    uri = source.as_uri()
    metadata = {"epub_cover": {"cache_name": name}}
    source.write_bytes(EMBEDDED_PNG)
    assert cached_cover_url(metadata) == uri
    assert loader.probe_media(uri).source_cached

    # Replacement remains a valid-looking PNG, but no longer matches its SHA.
    source.write_bytes(EMBEDDED_PNG + b"corrupted")
    assert cached_cover_url(metadata) is None
    state = loader.probe_media(uri, width=60, height=90)
    assert state.source_kind == "embedded"
    assert not state.source_cached and not state.preview_cached

    returned = []
    loader.load(uri, returned.append)
    assert returned == [None]
    assert loader.stats()["download_ok"] == 0
    assert loader.stats()["download_failed"] == 0
    # N3 physical invalidation never invents or revokes a documentary N2 proof.


def test_embedded_probe_does_not_recreate_absent_cache_directory(loader):
    root = cover_cache_dir(create=False)
    if root.exists():
        root.rmdir()
    source = root / (hashlib.sha256(EMBEDDED_PNG).hexdigest() + ".png")
    assert not loader.probe_media(source.as_uri()).source_cached
    assert not root.exists()
