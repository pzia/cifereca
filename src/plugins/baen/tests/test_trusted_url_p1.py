"""#1917: untrusted Baen HTML must not select arbitrary HTTP targets."""
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_baen import BaenSession, _trusted_baen_url, _public_epub_filename

BASE = "https://www.baen.com"


class FakeResponse:
    def __init__(self, body="", status=200):
        self.text = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("Test does not expect an HTTP error")


class FakeClient:
    def __init__(self, response):
        self.response=response
        self.calls=[]

    def get(self, url, **kwargs):
        self.calls.append((url,kwargs))
        return self.response

    def close(self):
        pass


class FakeTransport:
    def __init__(self):
        self.calls=[]

    def fetch(self, source, target):
        self.calls.append(source)
        return target


def rig(body="", status=200):
    s=BaenSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=FakeTransport(),
    )
    s.client.close()
    s.client=FakeClient(FakeResponse(body,status))
    s.connected=True
    return s


def item(url):
    return SimpleNamespace(
        source_provider="baen", friendly_url=url, title="Book",
        record_id="record", author=None, type="Livre numérique",
        thumbnail_url=None, published_at=None, metadata={},
    )


@pytest.mark.parametrize("bad", (
    "https://evil.example/allbooks/book",
    "http://www.baen.com/allbooks/book",
    "https://user:password@www.baen.com/allbooks/book",
    "https://www.baen.com:8443/allbooks/book",
    "https://www.baen.com/allbooks/book#fragment",
    "https://www.baen.com/allbooks/book\nHost:evil.example",
    "file:///etc/passwd",
))
def test_public_item_url_is_rejected_before_any_http(bad,tmp_path):
    s=rig()
    try:
        assert not _trusted_baen_url(bad)
        with pytest.raises(ProviderCapabilityError,match="untrusted_url"):
            s.holdings(item(bad))
        with pytest.raises(ProviderCapabilityError,match="untrusted_url"):
            s.download_direct(item(bad),tmp_path/"downloads")
        assert s.client.calls == [] and s.transport.calls == []
        assert not (tmp_path/"downloads").exists()
    finally:
        s.close()


def test_search_skips_off_origin_and_keeps_only_baen_cards():
    html = ('''<a href="https://evil.example/allbooks/book">External</a>'''
            '''<a href="/allbooks/category/book">Valid</a>'''
            '''<a href="//evil.example/allbooks/other">Injected</a>''')
    s=rig(html)
    try:
        rows=s.search("free")
        assert [x.title for x in rows] == ["Valid"]
        assert rows[0].friendly_url == BASE + "/allbooks/category/book"
        assert len(s.client.calls)==1
    finally:
        s.close()


@pytest.mark.parametrize("kind", ("search","holdings","download"))
def test_redirect_is_not_followed_or_presented_as_valid(kind,tmp_path):
    s=rig(status=302)
    try:
        with pytest.raises(ProviderError,match="public_redirect_unverified"):
            if kind=="search":
                s.search("book")
            elif kind=="holdings":
                s.holdings(item(BASE + "/allbooks/book"))
            else:
                s.download_direct(item(BASE + "/allbooks/book"),tmp_path)
        assert len(s.client.calls)==1 and s.transport.calls==[]
    finally:
        s.close()


def test_download_skips_external_html_epub_link(tmp_path):
    s=rig(
        '<a href="https://evil.example/books/book.epub">Download EPUB</a>'
    )
    try:
        with pytest.raises(ProviderError,match="aucun lien EPUB public explicite"):
            s.download_direct(item(BASE + "/allbooks/book"),tmp_path)
        assert len(s.client.calls)==1
        assert s.transport.calls==[]
        assert not tmp_path.exists() or not list(tmp_path.iterdir())
    finally:
        s.close()


def test_download_keeps_same_origin_epub_link(tmp_path):
    s=rig('<a href="/downloads/book.epub">Download EPUB</a>')
    try:
        result,_=s.download_direct(item(BASE + "/allbooks/book"),tmp_path)
        assert result.name == _public_epub_filename("Book", BASE + "/allbooks/book")
        assert len(s.transport.calls)==1
        assert s.transport.calls[0]["url"]==BASE + "/downloads/book.epub"
    finally:
        s.close()
