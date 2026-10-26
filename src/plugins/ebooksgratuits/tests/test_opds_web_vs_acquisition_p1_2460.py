"""#2460 — exact OPDS Web-vs-feed-vs-EPUB action contract (offline).

An Atom entry is synthetic, never a live source or a private purchase.
No acquisition happens on search; explicit broker calls are recorded only.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_ebooksgratuits import DESCRIPTOR, EbooksSession, _opds_items, _xml_root

HOST = "https://opds2.ebooksgratuits.org"
FEED = HOST + "/index.php/feed/search?q=book"
EPUB = HOST + "/index.php/fetch/42.epub"


def entry(*links):
    body = "".join(
        '<link rel="%s" type="%s" href="%s"/>' % (rel, mime, href)
        for rel, mime, href in links
    )
    return ('<feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><id>urn:example:42</id><title>Livre public</title>'
            + body + '</entry></feed>')


ACQUISITION = (
    "http://opds-spec.org/acquisition/open-access",
    "application/epub+zip", "/index.php/fetch/42.epub",
)


class RecordingTransport:
    def __init__(self):
        self.calls = []
    def fetch(self, source, target):
        self.calls.append((deepcopy(source), Path(target)))
        Path(target).write_bytes(b"SYNTHETIC-broker-test")
        return Path(target)


def session():
    obj = EbooksSession(
        credential_id="public", label="Public", credentials={},
        options={}, config={}, diagnostics=None,
        transport=RecordingTransport(),
    )
    obj.connect()
    return obj


def book(s, *links):
    rows = _opds_items(s, _xml_root(entry(ACQUISITION, *links).encode()), FEED)
    assert len(rows) == 1
    return rows[0]



def test_human_web_homepage_does_not_point_at_blank_cops_root():
    # A user clicking the supplier's Web page must not land at the COPS
    # host root; the feed and per-book acquisition URLs stay independent.
    assert DESCRIPTOR.homepage == "https://www.ebooksgratuits.com/"
    s = session()
    try:
        assert s.opds_feed_url == "https://opds2.ebooksgratuits.org/index.php/feed"
        assert DESCRIPTOR.homepage != s.opds_feed_url
        assert s.transport.calls == []
    finally:
        s.close()


def test_atom_only_alternate_is_not_web_page_and_download_is_separate():
    s = session()
    try:
        item = book(s, ("alternate", "application/atom+xml",
                        "/index.php/feed/item/42"))
        assert item.friendly_url is None
        assert item.download_url == EPUB
        assert item.downloadable and not item.owned
        assert s.transport.calls == []  # No download from a displayed row.
    finally:
        s.close()


@pytest.mark.parametrize("bad_mime,bad_url", [
    ("text/html", "/index.php/feed/item/42"),
    ("text/html", "/feed/item/42"),
    ("text/html", "/fetch/42.epub"),
    ("text/html", "/index.php/%2566eed/item/42"),
    ("text/html", "/index.php/%252566eed/item/42"),
    ("text/html", "/index.php/%2566etch/42.epub"),
    ("application/atom+xml", "/index.php/page/book/42"),
    ("text/html", "/index.php/fetch/42.epub"),
    ("text/html", "/index.php/account/private"),
    ("text/html", "/index.php/book/42?token=PRIVATE"),
    ("text/html", "https://evil.invalid/index.php/book/42"),
    ("text/html", "/index.php/opensearch.xml"),
    ("text/html", "/index.php/book/42%2f..%2faccount"),
])
def test_false_web_links_are_not_promoted_even_if_declared_alternate(
    bad_mime, bad_url,
):
    s = session()
    try:
        item = book(s, ("alternate", bad_mime, bad_url))
        assert item.friendly_url is None
        assert item.downloadable and item.download_url == EPUB
        assert not item.owned
    finally:
        s.close()


def test_real_html_alternate_kept_only_for_exact_entry_and_not_a_download():
    s = session()
    try:
        item = book(s, ("alternate", "text/html; charset=UTF-8",
                        "/index.php/page/book/42"))
        assert item.friendly_url == HOST + "/index.php/page/book/42"
        assert item.download_url == EPUB
        assert item.owned is False
        assert s.transport.calls == []
    finally:
        s.close()


@pytest.mark.parametrize("cached", [False, True])
def test_explicit_epub_broker_receives_same_origin_policy_with_or_without_cache(
    tmp_path, cached,
):
    s = session()
    try:
        fresh = book(s)
        row = deepcopy(fresh) if cached else fresh
        path, source_url = s.download_direct(row, tmp_path)
        assert path.is_file()
        assert source_url == EPUB
        assert len(s.transport.calls) == 1
        source = s.transport.calls[0][0]
        assert source["url"] == EPUB
        assert source["redirect_policy"] == "same_origin"
        assert source["classify_http_status"] is True
        assert source["max_redirects"] == 2
        assert source["expected_format"] == "epub"
        assert source["headers"]["Referer"] == s.opds_feed_url
        assert "www.ebooksgratuits.com" not in repr(source)
    finally:
        s.close()


def test_html_source_is_used_only_as_public_referrer(tmp_path):
    s = session()
    try:
        row = book(s, ("alternate", "text/html", "/index.php/page/book/42"))
        dest, url = s.download_direct(row, tmp_path)
        assert dest.is_file() and url == row.friendly_url
        assert s.transport.calls[0][0]["headers"]["Referer"] == row.friendly_url
        assert s.transport.calls[0][0]["url"] == EPUB
    finally:
        s.close()


@pytest.mark.parametrize("cached,bad_web", [
    (False, FEED),
    (True, FEED),
    (True, HOST + "/index.php/feed/item/42"),
    (True, HOST + "/index.php/book/42?token=PRIVATE"),
    (True, "https://foreign.invalid/not-the-book?sig=PRIVATE"),
    (True, HOST + "/index.php/%252566eed/42"),
])
def test_bad_cached_xml_or_foreign_web_link_does_not_block_exact_epub(
    tmp_path, cached, bad_web,
):
    s = session()
    try:
        fresh = book(s)
        row = deepcopy(fresh) if cached else fresh
        row.friendly_url = bad_web  # Historical cached HTML/Atom conflation.
        dest, returned = s.download_direct(row, tmp_path)
        assert dest.is_file()
        assert returned == EPUB
        assert len(s.transport.calls) == 1
        source = s.transport.calls[0][0]
        assert source["url"] == EPUB
        assert source["headers"]["Referer"] == s.opds_feed_url
        assert source["redirect_policy"] == "same_origin"
        assert source["expected_format"] == "epub"
        assert bad_web not in repr(source)
        assert "PRIVATE" not in repr(source)
    finally:
        s.close()


@pytest.mark.parametrize("invalid_epub", [
    FEED,
    HOST + "/index.php/account/private.epub",
    "https://foreign.invalid/index.php/fetch/42.epub",
    HOST + "/index.php/fetch/%2e%2e/account/private.epub",
])
def test_invalid_epub_is_still_refused_even_with_good_or_bad_web_link(
    tmp_path, invalid_epub,
):
    s = session()
    try:
        row = book(s, ("alternate", "text/html", "/index.php/page/book/42"))
        row.download_url = invalid_epub
        with pytest.raises(ProviderCapabilityError, match="invalid_catalog_link"):
            s.download_direct(row, tmp_path)
        assert not s.transport.calls
    finally:
        s.close()


def test_pdf_only_remains_non_downloadable_without_invented_epub(tmp_path):
    s = session()
    try:
        rows = _opds_items(s, _xml_root(entry((
            "http://opds-spec.org/acquisition/open-access",
            "application/pdf", "/index.php/fetch/42.pdf",
        )).encode()), FEED)
        assert len(rows) == 1
        assert rows[0].friendly_url is None and rows[0].download_url is None
        assert rows[0].downloadable is False and rows[0].owned is False
        with pytest.raises(ProviderError, match="offre inconnue"):
            s.download_direct(rows[0], tmp_path)
        assert s.transport.calls == []
    finally:
        s.close()
