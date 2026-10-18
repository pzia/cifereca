"""#1917: untrusted catalogue HTML cannot select cross-origin download URLs.

No live provider is contacted. A local origin with a nonstandard port remains
valid for the checked-in simulated EbooksGratuits server.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_ebooksgratuits import EbooksSession, Parser, _safe_catalog_link

BASE = "https://www.ebooksgratuits.com/"


def html_card(detail='details.php?book=1', epub='newsendbook.php?id=1&amp;format=epub'):
    return ('<ul><li><span class="listeb">Un livre</span>'
            f'<a href="{detail}">Détails</a>'
            f'<a href="{epub}">EPUB</a></li></ul>')


@pytest.mark.parametrize("bad", (
    "https://evil.example/details.php?book=1",
    "//evil.example/details.php?book=1",
    "http://www.ebooksgratuits.com/details.php?book=1",
    "https://user:pass@www.ebooksgratuits.com/details.php?book=1",
    "https://www.ebooksgratuits.com:8443/details.php?book=1",
    "https://www.ebooksgratuits.com/other.php?next=details.php",
    "https://www.ebooksgratuits.com/details.php?book=1#fragment",
    "details.php?book=1\nHost:evil.example",
))
def test_parser_does_not_publish_malicious_detail_url(bad):
    assert Parser().rows(html_card(detail=bad)) == []


@pytest.mark.parametrize("bad", (
    "https://evil.example/newsendbook.php?id=1&format=epub",
    "//evil.example/dl/book.epub",
    "http://www.ebooksgratuits.com/newsendbook.php?format=epub",
    "https://user@www.ebooksgratuits.com/book.epub",
    "https://www.ebooksgratuits.com:444/dl/book.epub",
    "javascript:alert('newsendbook.php?format=epub')",
    "newsendbook.php?format=pdf&id=1",
    "newsendbook.php?format=epub&format=pdf",
    "newsendbook.php?format=epub&flag",
    "https://www.ebooksgratuits.com/other.php?url=book.epub",
    "book.epub#fragment",
))
def test_parser_does_not_publish_malicious_epub_url(bad):
    assert Parser().rows(html_card(epub=bad.replace("&", "&amp;"))) == []


def test_parser_keeps_same_origin_realistic_links_and_local_simulation():
    normal=Parser().rows(html_card())
    assert len(normal) == 1
    assert normal[0]["detail"] == BASE + "details.php?book=1"
    assert normal[0]["epub"] == BASE + "newsendbook.php?id=1&format=epub"
    local="http://127.0.0.1:38421/"
    local_rows=Parser(local).rows(html_card(epub="downloads/1.epub"))
    assert len(local_rows) == 1
    assert local_rows[0]["detail"] == local + "details.php?book=1"
    assert local_rows[0]["epub"] == local + "downloads/1.epub"
    assert _safe_catalog_link(BASE, "downloads/1.epub", "epub") == BASE + "downloads/1.epub"


class Transport:
    def __init__(self):
        self.calls=[]
    def fetch(self,source,target):
        self.calls.append(source)
        return target


def session():
    s=EbooksSession(
        credential_id="public",label="",credentials={},options={},
        config={},diagnostics=None,transport=Transport(),
    )
    return s


@pytest.mark.parametrize("bad_download,bad_detail", (
    ("https://evil.example/downloads/book.epub", None),
    ("http://www.ebooksgratuits.com/downloads/book.epub", None),
    ("https://www.ebooksgratuits.com:444/downloads/book.epub", None),
    ("https://www.ebooksgratuits.com/newsendbook.php?format=pdf", None),
    (None, "https://evil.example/details.php?book=1"),
    (None, BASE + "account/reset?next=details.php"),
))
def test_direct_download_rejects_forged_urls_before_transport(tmp_path,bad_download,bad_detail):
    s=session()
    item=SimpleNamespace(
        source_provider="ebooksgratuits", title="Livre", record_id="book",
        download_url=bad_download or BASE + "downloads/book.epub",
        friendly_url=bad_detail or BASE + "details.php?book=1",
    )
    try:
        destination=tmp_path/"should_not_exist"
        with pytest.raises(ProviderCapabilityError,match="invalid_catalog_link"):
            s.download_direct(item,destination)
        assert s.transport.calls == []
        assert not destination.exists()
    finally:
        s.close()


def test_direct_download_keeps_valid_simulator_url(tmp_path):
    s=session()
    item=SimpleNamespace(
        source_provider="ebooksgratuits", title="Livre", record_id="book",
        download_url=BASE + "newsendbook.php?id=1&format=epub",
        friendly_url=BASE + "details.php?book=1",
    )
    try:
        destination,_=s.download_direct(item,tmp_path)
        assert destination.parent == tmp_path
        assert len(s.transport.calls)==1
        assert s.transport.calls[0]["url"] == item.download_url
    finally:
        s.close()
