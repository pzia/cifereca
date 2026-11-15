"""#6168 N3->GTK: reuse physical EPUB bytes, never infer N2 book association."""
from __future__ import annotations

import hashlib
from pathlib import Path
from threading import Event

import pytest

from cifereca import epub_metadata
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.epub_cover_materialization import ExactEpubCoverResource
from cifereca.gui.cover_loader import CoverLoader
from tests.test_epub_cover_contract import _write_epub


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "original.epub"
    raw = _write_epub(book, epub3=True)
    metadata = epub_metadata.inspect_epub(book)
    assert metadata["epub_cover_status"] == "extracted"
    source = ExactEpubCoverResource(
        scope=CoverDocumentScope(
            kind="placement", identity_id="alice", source_key="book:a",
            artifact_id="artifact:a", placement_id="placement:a",
        ),
        source_revision=3, artifact_sha256=hashlib.sha256(raw).hexdigest(),
        member=metadata["epub_cover"]["member"],
        cache_name=metadata["epub_cover"]["cache_name"],
    )
    local = epub_metadata.cover_cache_dir() / source.cache_name
    loader = CoverLoader(max_workers=2)
    try:
        yield book, source, local, loader
    finally:
        loader.close()


def test_warm_embedded_image_never_opens_source_epub(prepared, monkeypatch):
    book, source, local, loader = prepared
    book.unlink()
    def forbidden(*args):
        raise AssertionError("no EPUB open on N3 cache hit")
    monkeypatch.setattr(loader._epub_physical, "_restore", forbidden)
    result = []
    loader.load_exact_epub(source, source_path=book, callback=result.append)
    assert result == [local]
    assert loader.stats()["download_ok"] == 0


def test_cold_embedded_image_rehydrates_once_for_list_and_detail(prepared, monkeypatch):
    book, source, local, loader = prepared
    local.unlink()
    entered, proceed, list_ready, detail_ready = Event(), Event(), Event(), Event()
    calls = []
    original = loader._epub_physical._restore

    def gated(ref, path):
        calls.append((ref, path))
        entered.set()
        assert proceed.wait(8)
        return original(ref, path)

    monkeypatch.setattr(loader._epub_physical, "_restore", gated)
    listing, detail = [], []

    def on_list(path):
        listing.append(path)
        list_ready.set()

    def on_detail(path):
        detail.append(path)
        detail_ready.set()

    list_req = loader.load_exact_epub(
        source, source_path=book, callback=on_list, width=180, height=270,
    )
    assert entered.wait(8)
    detail_req = loader.load_exact_epub(
        source, source_path=book, callback=on_detail,
    )
    try:
        assert loader._epub_physical.stats()["inflight"] == 1
        proceed.set()
        assert list_ready.wait(8) and detail_ready.wait(8)
        assert list_req is not detail_req
        assert len(calls) == 1
        assert detail == [local]
        assert len(listing) == 1 and listing[0] is not None
        assert loader.probe_media(local.as_uri()).source_cached
        assert loader.stats()["download_ok"] == 0
        assert loader.stats()["download_failed"] == 0
    finally:
        proceed.set()


def test_cancelled_cell_does_not_receive_late_epub_recovery(prepared, monkeypatch):
    book, source, local, loader = prepared
    local.unlink()
    entered, proceed, received = Event(), Event(), Event()
    original = loader._epub_physical._restore

    def gated(ref, path):
        entered.set()
        assert proceed.wait(8)
        return original(ref, path)

    monkeypatch.setattr(loader._epub_physical, "_restore", gated)
    discarded = []
    stale = loader.load_exact_epub(source, source_path=book, callback=discarded.append)
    assert entered.wait(8)
    live = []
    req = loader.load_exact_epub(
        source, source_path=book,
        callback=lambda value: (live.append(value), received.set()),
    )
    stale.cancel()
    try:
        proceed.set()
        assert received.wait(8)
        assert discarded == []
        assert live == [local]
        assert req is not stale
    finally:
        proceed.set()


def test_corrupt_or_changed_epub_never_triggers_remote_get(prepared):
    book, source, local, loader = prepared
    local.unlink()
    book.write_bytes(book.read_bytes() + b"changed")
    ready = Event()
    result = []
    loader.load_exact_epub(
        source, source_path=book,
        callback=lambda value: (result.append(value), ready.set()),
    )
    assert ready.wait(8)
    assert result == [None]
    assert not local.exists()
    assert loader.stats()["download_ok"] == 0
    assert loader.stats()["download_failed"] == 0


@pytest.mark.parametrize("kwargs", [
    {"width": 0, "height": 20},
    {"width": 90, "height": None},
    {"width": None, "height": 20},
    {"width": True, "height": 10},
    {"width": 90, "height": 2049},
])
def test_invalid_preview_request_never_starts_epub_work(prepared, kwargs):
    book, source, _local, loader = prepared
    with pytest.raises(ValueError):
        loader.load_exact_epub(
            source, source_path=book, callback=lambda result: None, **kwargs,
        )


def test_untrusted_source_or_callback_is_rejected_before_io(prepared):
    book, source, _local, loader = prepared
    with pytest.raises(TypeError):
        loader.load_exact_epub(object(), source_path=book, callback=lambda value: None)
    with pytest.raises(TypeError):
        loader.load_exact_epub(source, source_path=book, callback=None)
