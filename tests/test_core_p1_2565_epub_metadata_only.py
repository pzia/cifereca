"""P1 #2565: OPF-only observation must never create a cover/cache/file.

All EPUBs are synthetic; no service, SQLite, account, network, or DRM.
The safe no-follow directory/file opening is a separate reconciliation step.
"""
from __future__ import annotations

import json
import zipfile

from cifereca import epub_metadata


CONTAINER = (
    "<?xml version='1.0'?>"
    "<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
    "<rootfiles><rootfile full-path='OPS/content.opf'/></rootfiles>"
    "</container>"
)
OPF = (
    "<package xmlns='http://www.idpf.org/2007/opf'"
    " xmlns:dc='http://purl.org/dc/elements/1.1/' version='3.0'>"
    "<metadata><dc:title>FICTIONAL_EPUB_TITLE</dc:title>"
    "<dc:creator>FICTIONAL_AUTHOR</dc:creator>"
    "<dc:identifier scheme='ISBN'>9781234567890</dc:identifier>"
    "<dc:publisher>FICTIONAL_PUBLISHER</dc:publisher>"
    "<dc:language>fr</dc:language>"
    "<meta name='calibre:series' content='FICTIONAL_SERIES'/>"
    "</metadata>"
    "<manifest><item id='cover' properties='cover-image'"
    " href='images/cover.png' media-type='image/png'/></manifest>"
    "<spine/></package>"
)


def _book(tmp_path, *, corrupt=False):
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OPS/content.opf", "<!DOCTYPE x><package/>" if corrupt else OPF)
        archive.writestr("OPS/images/cover.png", b"FICTIONAL_COVER_BYTES")
    return path


def test_metadata_only_preserves_opf_provenance_without_cover_or_writes(
    tmp_path, monkeypatch,
):
    book = _book(tmp_path)
    original = book.read_bytes()
    def never_cover(*_args, **_kwargs):
        raise AssertionError("metadata-only mode must not access the cover cache")
    monkeypatch.setattr(epub_metadata, "cover_cache_dir", never_cover)
    monkeypatch.setattr(epub_metadata, "_publish_cover", never_cover)
    original_read = epub_metadata._read
    names = []
    def checked_read(archive, name, limit):
        names.append(name)
        assert name in {"META-INF/container.xml", "OPS/content.opf"}
        return original_read(archive, name, limit)
    monkeypatch.setattr(epub_metadata, "_read", checked_read)

    result = epub_metadata.inspect_epub(book, metadata_only=True)

    assert result["epub_status"] == "ok"
    assert result["epub_cover_status"] == "not_inspected"
    assert "epub_cover" not in result
    assert names == ["META-INF/container.xml", "OPS/content.opf"]
    meta = result["epub_metadata"]
    assert meta["title"] == "FICTIONAL_EPUB_TITLE"
    assert meta["authors"] == ["FICTIONAL_AUTHOR"]
    assert meta["identifiers"] == [
        {"value": "9781234567890", "scheme": "ISBN", "source": "epub"}
    ]
    assert meta["publishers"] == ["FICTIONAL_PUBLISHER"]
    assert meta["languages"] == ["fr"]
    assert meta["series"] == [{"name": "FICTIONAL_SERIES", "index": None}]
    assert meta["source"] == "epub"
    assert book.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["book.epub"]
    assert "FICTIONAL_EPUB_TITLE" in json.dumps(result)


def test_metadata_only_rejects_unsafe_opf_without_creating_cache(tmp_path, monkeypatch):
    book = _book(tmp_path, corrupt=True)
    before = book.read_bytes()
    def never_cover(*_args, **_kwargs):
        raise AssertionError("unsafe EPUB must not create cover cache")
    monkeypatch.setattr(epub_metadata, "cover_cache_dir", never_cover)

    result = epub_metadata.inspect_epub(book, metadata_only=True)

    assert result["epub_status"] == "unsafe_xml"
    assert result["epub_cover_status"] == "metadata_unreadable"
    assert result["epub_metadata"] == {}
    assert book.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["book.epub"]


def test_default_epub_cover_behavior_remains_enabled(tmp_path, monkeypatch):
    # A normal caller still follows the old cover path. The fixture's fake
    # PNG cannot publish a cover; this tests branch execution, not a cache.
    book = _book(tmp_path)
    calls = []
    original = epub_metadata._image_kind
    def seen(data):
        calls.append(True)
        return original(data)
    monkeypatch.setattr(epub_metadata, "_image_kind", seen)
    result = epub_metadata.inspect_epub(book)
    assert result["epub_status"] == "ok"
    assert result["epub_cover_status"] == "no_usable_image"
    assert calls
