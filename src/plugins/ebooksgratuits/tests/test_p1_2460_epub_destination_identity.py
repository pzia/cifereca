"""#2460: public downloads of homonymous books must not overwrite each other.

Only synthetic OPDS URLs and a fake file broker: no live network, DRM or rights.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca_ebooksgratuits import EbooksSession, _public_epub_filename


HOST = "https://opds2.ebooksgratuits.org"
FIRST = HOST + "/index.php/fetch/41.epub"
SECOND = HOST + "/index.php/fetch/42.epub"


class RecordingTransport:
    def __init__(self):
        self.calls = []

    def fetch(self, source, destination):
        path = Path(destination)
        self.calls.append((source, path))
        path.write_bytes(source["url"].encode("utf-8"))
        return path


def session():
    transport = RecordingTransport()
    obj = EbooksSession(
        credential_id="public", label="Public", credentials={}, options={},
        config={}, diagnostics=None, transport=transport,
    )
    obj.connect()
    return obj, transport


def item(url, *, title="Même titre", record_id="shared-record-id"):
    return SimpleNamespace(
        source_provider="ebooksgratuits",
        metadata={"acquisition_source": "opds-calibre"},
        download_url=url,
        friendly_url=None,
        title=title,
        record_id=record_id,
    )


def test_same_title_distinct_attested_offers_have_distinct_files(tmp_path):
    obj, broker = session()
    try:
        first, second = item(FIRST), item(SECOND)
        a, _ = obj.download_direct(first, tmp_path)
        b, _ = obj.download_direct(second, tmp_path)
        assert a != b
        assert a.parent == b.parent == tmp_path
        assert a.name.startswith("Même titre-") and b.name.startswith("Même titre-")
        assert a.read_bytes() == FIRST.encode()
        assert b.read_bytes() == SECOND.encode()
        assert len(broker.calls) == 2

        # A retried click or cached row keeps one stable destination.
        again, _ = obj.download_direct(first, tmp_path)
        assert again == a
        assert b.read_bytes() == SECOND.encode()
        assert len(list(tmp_path.glob("*.epub"))) == 2
    finally:
        obj.close()


@pytest.mark.parametrize("bad_title", ["   ", "///"])
def test_hostile_record_id_cannot_escape_target_directory(tmp_path, bad_title):
    obj, broker = session()
    try:
        # Previously an all-punctuation title fell back to raw record_id,
        # allowing '../' path components when the broker wrote the EPUB.
        malicious = item(FIRST, title=bad_title, record_id="../../outside")
        result, _ = obj.download_direct(malicious, tmp_path / "safe")
        assert result.parent == tmp_path / "safe"
        assert result.name.startswith("Livre-")
        assert ".." not in result.name and "/" not in result.name
        assert result.is_file()
        assert len(broker.calls) == 1
        assert not (tmp_path / "outside.epub").exists()
    finally:
        obj.close()


def test_very_long_unicode_title_keeps_filename_under_filesystem_limit(tmp_path):
    obj, broker = session()
    try:
        title = "📚" * 1000
        result, _ = obj.download_direct(item(FIRST, title=title), tmp_path)
        assert len(result.name.encode("utf-8")) <= 255
        assert result.is_file()
        assert len(broker.calls) == 1
    finally:
        obj.close()


def test_acquisition_url_and_record_id_do_not_appear_in_filename():
    name = _public_epub_filename("Livre", FIRST + "?token=PRIVATE_VALUE")
    assert name.endswith(".epub")
    assert "PRIVATE_VALUE" not in name
    assert "fetch" not in name and "41.epub" not in name
    assert "../" not in name
