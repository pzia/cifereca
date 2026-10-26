"""#2333: Baen public EPUB output paths are bound to an attested product URL."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cifereca_baen import BaenSession, _public_epub_filename

BASE = "https://www.baen.com"
FIRST = BASE + "/allbooks/free/first"
SECOND = BASE + "/allbooks/free/second"


class FakeResponse:
    status_code = 200
    text = '<a href="/downloads/public-book.epub">Download EPUB</a>'

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse()

    def close(self):
        pass


class FileTransport:
    def __init__(self):
        self.calls = []

    def fetch(self, source, destination):
        target = Path(destination)
        self.calls.append((source, target))
        target.write_bytes(source["headers"]["Referer"].encode("utf-8"))
        return target


def session():
    obj = BaenSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=FileTransport(),
    )
    obj.client.close()
    obj.client = FakeClient()
    obj.connect()
    return obj


def book(url, *, title="Same title", record_id="repeated-id"):
    return SimpleNamespace(
        source_provider="baen", friendly_url=url, title=title,
        record_id=record_id, metadata={}, author=None, published_at=None,
    )


def test_different_public_products_with_same_title_do_not_clobber(tmp_path):
    obj = session()
    try:
        a, _ = obj.download_direct(book(FIRST), tmp_path)
        b, _ = obj.download_direct(book(SECOND), tmp_path)
        assert a != b
        assert a.name == _public_epub_filename("Same title", FIRST)
        assert b.name == _public_epub_filename("Same title", SECOND)
        assert a.read_bytes() == FIRST.encode()
        assert b.read_bytes() == SECOND.encode()
        again, _ = obj.download_direct(book(FIRST), tmp_path)
        assert again == a
        assert b.read_bytes() == SECOND.encode()
        assert len(obj.transport.calls) == 3
        assert len(tuple(tmp_path.glob("*.epub"))) == 2
    finally:
        obj.close()


def test_empty_sanitized_title_never_uses_untrusted_record_id(tmp_path):
    obj = session()
    try:
        result, _ = obj.download_direct(
            book(FIRST, title="///", record_id="../../outside"), tmp_path / "safe",
        )
        assert result.parent == tmp_path / "safe"
        assert result.name.startswith("Livre-")
        assert ".." not in result.name and "/" not in result.name
        assert result.is_file()
        assert not (tmp_path / "outside.epub").exists()
    finally:
        obj.close()


def test_long_unicode_titles_and_signed_parameters_not_published(tmp_path):
    obj = session()
    try:
        result, _ = obj.download_direct(book(FIRST, title="📚" * 500), tmp_path)
        assert result.is_file()
        assert len(result.name.encode("utf-8")) <= 255
        name = _public_epub_filename("Livre", FIRST + "?token=PRIVATE_VALUE")
        assert "PRIVATE_VALUE" not in name
        assert "allbooks" not in name
        assert name.endswith(".epub")
    finally:
        obj.close()
