from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from cifereca import epub_metadata


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (2).to_bytes(4, "big") + (3).to_bytes(4, "big") + b"payload"


def _write_epub(path: Path, *, epub3: bool) -> bytes:
    if epub3:
        metadata = "<dc:title xmlns:dc='http://purl.org/dc/elements/1.1/'>Test</dc:title>"
        manifest = "<item id='cover' href='images/cover.png' media-type='image/png' properties='cover-image'/>"
    else:
        metadata = (
            "<dc:title xmlns:dc='http://purl.org/dc/elements/1.1/'>Test</dc:title>"
            "<meta name='cover' content='cover'/>"
        )
        manifest = "<item id='cover' href='images/cover.png' media-type='image/png'/>"
    container = (
        "<?xml version='1.0'?>"
        "<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
        "<rootfiles><rootfile full-path='OPS/content.opf'/></rootfiles></container>"
    )
    opf = (
        "<?xml version='1.0'?>"
        "<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>"
        f"<metadata>{metadata}</metadata>"
        f"<manifest>{manifest}</manifest>"
        "<spine/></package>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/content.opf", opf)
        archive.writestr("OPS/images/cover.png", PNG)
    return path.read_bytes()


def _cache(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "cover-cache"
    root.mkdir()
    monkeypatch.setattr(epub_metadata, "cover_cache_dir", lambda: root)
    return root


def test_epub3_declared_cover_is_extracted_and_original_preserved(tmp_path, monkeypatch):
    cache = _cache(monkeypatch, tmp_path)
    book = tmp_path / "book.epub"
    before = _write_epub(book, epub3=True)
    digest = hashlib.sha256(before).hexdigest()

    result = epub_metadata.inspect_epub(book)

    assert result["epub_status"] == "ok"
    assert result["epub_cover_status"] == "extracted"
    assert result["epub_cover"]["method"] == "declared"
    assert result["epub_cover"]["member"] == "OPS/images/cover.png"
    assert (cache / result["epub_cover"]["cache_name"]).read_bytes() == PNG
    assert hashlib.sha256(book.read_bytes()).hexdigest() == digest


def test_epub2_meta_cover_is_extracted(tmp_path, monkeypatch):
    _cache(monkeypatch, tmp_path)
    book = tmp_path / "book.epub"
    _write_epub(book, epub3=False)

    result = epub_metadata.inspect_epub(book)

    assert result["epub_status"] == "ok"
    assert result["epub_cover"]["method"] == "declared"
    assert result["epub_cover"]["member"] == "OPS/images/cover.png"


def test_cover_cache_is_content_addressed_reused_after_move_and_rebuilt_if_corrupt(tmp_path, monkeypatch):
    cache = _cache(monkeypatch, tmp_path)
    first = tmp_path / "first.epub"
    raw = _write_epub(first, epub3=True)
    initial = epub_metadata.inspect_epub(first)
    name = initial["epub_cover"]["cache_name"]
    cached = cache / name
    mtime = cached.stat().st_mtime_ns

    moved = tmp_path / "moved.epub"
    moved.write_bytes(raw)
    again = epub_metadata.inspect_epub(moved)
    assert again["epub_cover"]["cache_name"] == name
    assert cached.stat().st_mtime_ns == mtime
    assert epub_metadata.cached_cover_url(again) == cached.resolve().as_uri()

    cached.write_bytes(b"corrupt")
    assert epub_metadata.cached_cover_url(again) is None
    rebuilt = epub_metadata.inspect_epub(moved)
    assert rebuilt["epub_cover"]["cache_name"] == name
    assert cached.read_bytes() == PNG


def test_oversized_opf_is_rejected_before_resource_extraction(tmp_path, monkeypatch):
    cache = _cache(monkeypatch, tmp_path)
    book = tmp_path / "oversized.epub"
    container = (
        "<?xml version='1.0'?>"
        "<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
        "<rootfiles><rootfile full-path='OPS/content.opf'/></rootfiles></container>"
    )
    with zipfile.ZipFile(book, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/content.opf", b"x" * (epub_metadata.MAX_XML + 1))
        archive.writestr("OPS/images/cover.png", PNG)

    result = epub_metadata.inspect_epub(book)

    assert result["epub_status"] == "oversized_or_symlink_entry"
    assert result["epub_cover_status"] == "metadata_unreadable"
    assert list(cache.iterdir()) == []
