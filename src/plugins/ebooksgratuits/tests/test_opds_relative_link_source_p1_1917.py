"""#1917 / #2460: a relative Atom link belongs to its containing feed URI.

All feeds and acquisition paths are synthetic. MockTransport ensures that
no live OPDS site, account, file, or acquisition endpoint is contacted.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca_ebooksgratuits import (
    EbooksSession, _opds_items, _opds_source_link, _xml_root,
)

HOST = "https://opds2.ebooksgratuits.org"
ROOT_URL = HOST + "/index.php/feed"
SEARCH = HOST + "/index.php/feed/search/jefferson"
EPUB = HOST + "/index.php/fetch/42.epub"
WEB = HOST + "/index.php/page/book/42"
ATOM = "http://www.w3.org/2005/Atom"

ROOT = f'''<feed xmlns="{ATOM}">
<link rel="search" type="application/atom+xml"
 href="/index.php/feed/search/{{searchTerms}}"/>
</feed>'''

BOOK = f'''<feed xmlns="{ATOM}"><entry>
<id>urn:book:42</id><title>Jefferson</title>
<author><name>Jean-Claude Mourlevat</name></author>
<link rel="alternate" type="application/epub+zip"
 href="../../fetch/42.epub"/>
<link rel="alternate" type="text/html"
 href="../../page/book/42"/>
<link rel="http://opds-spec.org/image/thumbnail" type="image/jpeg"
 href="../../covers/42.jpg"/>
</entry></feed>'''

SEARCH_NAV = f'''<feed xmlns="{ATOM}"><entry>
<id>urn:facet:authors</id><title>Livres et auteurs</title>
<link rel="subsection" type="application/atom+xml" href="../authors"/>
</entry></feed>'''

AUTHOR_BOOK = f'''<feed xmlns="{ATOM}"><entry>
<id>urn:book:42</id><title>Jefferson</title>
<author><name>Jean-Claude Mourlevat</name></author>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="../fetch/42.epub"/>
</entry></feed>'''


def provider(pages):
    visited = []

    def respond(request):
        visited.append(str(request.url))
        content = pages.get(str(request.url))
        if content is None:
            pytest.fail("unexpected OPDS request " + str(request.url))
        return httpx.Response(200, text=content, request=request)

    s = EbooksSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    s.client.close()
    s.client = httpx.Client(
        transport=httpx.MockTransport(respond), trust_env=False,
    )
    s.connect()
    return s, visited


def test_nested_feed_relative_epub_and_html_are_exactly_resolved_without_download():
    s, visited = provider({ROOT_URL: ROOT, SEARCH: BOOK})
    try:
        found = s.search("jefferson", field="all")
        assert len(found) == 1
        row = found[0]
        assert row.title == "Jefferson"
        assert row.friendly_url == WEB
        assert row.thumbnail_url == HOST + "/index.php/covers/42.jpg"
        assert row.download_url == EPUB
        assert row.downloadable is True and row.owned is False
        assert row.metadata["acquisition_source"] == "opds-calibre"
        assert visited == [ROOT_URL, SEARCH]
    finally:
        s.close()


def test_opensearch_description_relative_template_uses_its_own_url():
    meta = HOST + "/index.php/feed/meta/search.xml"
    root = f'''<feed xmlns="{ATOM}"><link rel="search"
    type="application/opensearchdescription+xml"
    href="/index.php/feed/meta/search.xml"/></feed>'''
    opensearch = '''<OpenSearchDescription
    xmlns="http://a9.com/-/spec/opensearch/1.1/">
    <Url type="application/atom+xml"
     template="../search/{searchTerms}"/>
    </OpenSearchDescription>'''
    s, visited = provider({
        ROOT_URL: root, meta: opensearch, SEARCH: BOOK,
    })
    try:
        items = s.search("jefferson")
        assert len(items) == 1
        assert items[0].download_url == EPUB
        assert items[0].friendly_url == WEB
        assert visited == [ROOT_URL, meta, SEARCH]
    finally:
        s.close()


def test_author_browse_relative_subsection_uses_current_feed_not_root():
    authors = HOST + "/index.php/feed/authors"
    s, visited = provider({
        ROOT_URL: ROOT,
        SEARCH: SEARCH_NAV,
        authors: AUTHOR_BOOK,
    })
    try:
        found = s.search("jefferson", field="all")
        assert len(found) == 1
        assert found[0].download_url == EPUB
        assert found[0].friendly_url is None
        assert visited == [ROOT_URL, SEARCH, authors]
        assert found[0].owned is False
    finally:
        s.close()


def test_correct_field_author_still_explicitly_partial_after_relative_navigation():
    authors = HOST + "/index.php/feed/authors"
    s, visited = provider({
        ROOT_URL: ROOT,
        HOST + "/index.php/feed/search/Mourlevat": SEARCH_NAV,
        authors: AUTHOR_BOOK,
    })
    try:
        found = s.search("Mourlevat", field="author")
        assert len(found) == 1
        assert found[0].author == "Jean-Claude Mourlevat"
        assert found[0].download_url == EPUB
        assert any("partial_results" in warning
                   for warning in s.last_search_warnings)
        assert visited == [
            ROOT_URL, HOST + "/index.php/feed/search/Mourlevat", authors,
        ]
    finally:
        s.close()


@pytest.mark.parametrize("base,href,kind", [
    ("https://evil.invalid/index.php/feed/search/book",
     "../../fetch/42.epub", "download"),
    (SEARCH, "../../account/private.epub", "download"),
    (SEARCH, "../../fetch/%2e%2e/account/private.epub", "download"),
    (SEARCH, "https://evil.invalid/index.php/fetch/42.epub", "download"),
    (SEARCH, "../account/private", "feed"),
    (SEARCH, "http://opds2.ebooksgratuits.org/index.php/feed/other", "feed"),
])
def test_relative_link_cannot_use_untrusted_origin_or_private_route(base,href,kind):
    s, visited = provider({})
    try:
        assert _opds_source_link(s, base, href, kind) is None
        assert visited == []
    finally:
        s.close()


def test_absent_epub_does_not_invent_acquisition_from_a_html_alternate():
    html_only = f'''<feed xmlns="{ATOM}"><entry>
    <id>urn:book:42</id><title>Page HTML uniquement</title>
    <link rel="alternate" type="text/html"
    href="../../page/book/42"/></entry></feed>'''
    s, visited = provider({})
    try:
        rows = _opds_items(s, _xml_root(html_only.encode()), SEARCH)
        assert rows == []
        assert visited == []
    finally:
        s.close()
