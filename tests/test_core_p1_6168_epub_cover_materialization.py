"""#6168 N3: restore EPUB image bytes without re-resolving a DocumentRef."""
from __future__ import annotations

import hashlib
from pathlib import Path
from threading import Event

import pytest

from cifereca import epub_metadata
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.epub_cover_materialization import (
    EpubCoverPhysicalCache, ExactEpubCoverResource,
)
from tests.test_epub_cover_contract import _write_epub


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "book.epub"
    data = _write_epub(book, epub3=True)
    observed = epub_metadata.inspect_epub(book)
    assert observed["epub_cover_status"] == "extracted"
    image = observed["epub_cover"]
    scope = CoverDocumentScope(
        kind="placement", identity_id="alice", source_key="placement:42",
        artifact_id="artifact:42", placement_id="placement:42",
    )
    reference = ExactEpubCoverResource(
        scope=scope, source_revision=7, artifact_sha256=hashlib.sha256(data).hexdigest(),
        member=image["member"], cache_name=image["cache_name"],
    )
    local = epub_metadata.cover_cache_dir() / reference.cache_name
    assert local.is_file()
    return book, reference, local


def test_hot_epub_n3_never_reopens_or_reparses_source(prepared, monkeypatch):
    book, ref, image = prepared
    book.unlink()  # N3 is physical image cache, not N2 documentary authority.
    materializer = EpubCoverPhysicalCache()
    try:
        def not_called(*args):
            raise AssertionError("EPUB should not open on N3 HIT")
        monkeypatch.setattr(materializer, "_restore", not_called)
        answer = materializer.request(ref, source_path=book)
        assert answer.done() and answer.result(timeout=5) == image.resolve().as_uri()
        assert materializer.stats() == {"inflight": 0}
    finally:
        materializer.close()


def test_cold_epub_n3_rehydrates_exact_image_without_catalogue_rescan(prepared, monkeypatch):
    book, ref, image = prepared
    image.unlink()
    materializer = EpubCoverPhysicalCache()
    try:
        monkeypatch.setattr(
            epub_metadata, "inspect_epub",
            lambda *args, **kwargs: pytest.fail("N3 cannot rerun N1 EPUB parsing"),
        )
        result = materializer.request(ref, source_path=book).result(timeout=5)
        assert result == image.resolve().as_uri()
        assert epub_metadata.valid_cached_cover_path(image)
        assert book.exists()
    finally:
        materializer.close()


def test_simultaneous_list_and_detail_share_single_cold_work(prepared, monkeypatch):
    book, ref, image = prepared
    image.unlink()
    materializer = EpubCoverPhysicalCache(max_workers=2)
    arrived, release = Event(), Event()
    original = materializer._restore
    calls = []

    def delayed(source, path):
        calls.append((source, path))
        arrived.set()
        assert release.wait(5)
        return original(source, path)

    monkeypatch.setattr(materializer, "_restore", delayed)
    try:
        listing = materializer.request(ref, source_path=book)
        assert arrived.wait(5)
        detail = materializer.request(ref, source_path=book)
        assert listing is detail
        assert materializer.stats()["inflight"] == 1
        release.set()
        assert listing.result(timeout=5) == image.resolve().as_uri()
        assert calls == [(ref, book)]
    finally:
        release.set()
        materializer.close()


def test_stale_epub_artifact_never_rebuilds_image_for_another_file(prepared):
    book, ref, image = prepared
    image.unlink()
    book.write_bytes(book.read_bytes() + b"different-content")
    materializer = EpubCoverPhysicalCache()
    try:
        assert materializer.request(ref, source_path=book).result(timeout=5) is None
        assert not image.exists()
    finally:
        materializer.close()


@pytest.mark.parametrize("change", [
    {"source_revision": True},
    {"artifact_sha256": "not-a-digest"},
    {"member": "../foreign.png"},
    {"member": "https://bad.invalid/cover.png"},
    {"member": "OPS/../images/cover.png"},
    {"cache_name": "abc.png"},
])
def test_malformed_epub_provenance_is_rejected(prepared, change):
    from dataclasses import replace
    _book, ref, _image = prepared
    with pytest.raises(ValueError):
        replace(ref, **change)


def test_unverified_loan_or_different_placement_cannot_be_minted(prepared):
    from dataclasses import replace
    _book, ref, _image = prepared
    loan = CoverDocumentScope(
        kind="loan", identity_id="alice", source_key="loan:42",
        provider_id="paris", credential_id="card", account_id="",
        loan_id="42",
    )
    with pytest.raises(ValueError):
        replace(ref, scope=loan)
    cache = EpubCoverPhysicalCache()
    try:
        with pytest.raises(TypeError):
            cache.request(object(), source_path=Path("/tmp/other.epub"))
    finally:
        cache.close()
