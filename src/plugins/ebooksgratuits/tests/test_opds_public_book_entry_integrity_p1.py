"""P1 #1917: only safe, MIME-qualified OPDS acquisitions identify books.

Atom is synthetic, public and exclusively served from httpx.MockTransport.
No account, private library, signed download, live network or acquisition.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_ebooksgratuits import EbooksSession

BASE = "https://opds2.ebooksgratuits.org"
ROOT_URL = BASE + "/index.php/feed"

BOOK = """<entry><id>urn:public:book:42</id><title>Livre vraiment public</title>
<author><name>Autrice publiée</name></author>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="/index.php/fetch/42.epub"/></entry>"""
PDF = """<entry><id>urn:public:book:43</id><title>Notice uniquement PDF</title>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/pdf" href="/index.php/fetch/43.pdf"/></entry>"""
NAVIGATION = """<entry><id>urn:subsection:other</id><title>Plus de livres</title>
<link rel="subsection" type="application/atom+xml"
 href="/index.php/feed/other"/></entry>"""
BAD_FOREIGN = """<entry><id>urn:fake:foreign</id><title>Faux livre externe</title>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip"
 href="https://evil.invalid/index.php/fetch/credential.epub"/></entry>"""
BAD_PRIVATE = """<entry><id>urn:fake:private</id><title>Faux livre privé</title>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip"
 href="/index.php/account/export.epub"/></entry>"""
BAD_MIME = """<entry><id>urn:fake:html</id><title>Faux livre HTML</title>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="text/html" href="/index.php/fetch/42.epub"/></entry>"""
BAD_ALTERNATE = """<entry><id>urn:fake:alternate</id><title>Faux livre alternatif</title>
<link rel="alternate" type="application/epub+zip"
 href="/index.php/account/private.epub"/></entry>"""


def feed(entries):
    return '<feed xmlns="http://www.w3.org/2005/Atom">' + entries + '</feed>'


def branch():
    return """<entry><id>urn:root:all</id><title>All books</title>
<link rel="subsection" type="application/atom+xml"
 href="/index.php/feed/all"/></entry>"""


def session(root, *, all_books=None):
    visited = []

    def respond(request):
        visited.append(str(request.url))
        path = request.url.path
        if path == "/index.php/feed":
            source = root
        elif path == "/index.php/feed/all" and all_books is not None:
            source = all_books
        else:
            pytest.fail("unexpected OPDS GET " + path)
        return httpx.Response(200, text=source, request=request)

    s = EbooksSession(
        credential_id="public", label="Public", credentials={},
        options={}, config={}, diagnostics=None, transport=None,
    )
    s.client.close()
    s.client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    s.connect()
    return s, visited


@pytest.mark.parametrize("forged", [BAD_FOREIGN, BAD_PRIVATE, BAD_MIME, BAD_ALTERNATE])
def test_unqualified_root_acquisition_does_not_hide_all_books_route(forged):
    s, visited = session(feed(forged + branch()), all_books=feed(BOOK))
    try:
        rows = s.search("")
        assert len(rows) == 1
        assert rows[0].title == "Livre vraiment public"
        assert rows[0].downloadable and not rows[0].owned
        assert rows[0].download_url == BASE + "/index.php/fetch/42.epub"
        assert visited == [ROOT_URL, BASE + "/index.php/feed/all"]
        assert s.last_search_warnings == []
    finally:
        s.close()


@pytest.mark.parametrize("forged", [BAD_FOREIGN, BAD_PRIVATE, BAD_MIME, BAD_ALTERNATE])
def test_only_unqualified_root_entries_fail_as_unavailable_not_zero(forged):
    s, visited = session(feed(forged))
    try:
        with pytest.raises(ProviderCapabilityError, match="browse_route_unavailable") as exc:
            s.search("")
        assert "evil.invalid" not in str(exc.value)
        assert "private.epub" not in str(exc.value)
        assert visited == [ROOT_URL]
    finally:
        s.close()


def test_mixed_navigation_and_acquisitions_emit_partial_and_never_show_fake_book():
    s, visited = session(feed(branch()), all_books=feed(
        BOOK + NAVIGATION + BAD_FOREIGN + BAD_PRIVATE + BAD_MIME))
    try:
        rows = s.search("", field="all")
        assert [row.title for row in rows] == ["Livre vraiment public"]
        assert rows[0].source_collection == "public"
        assert rows[0].owned is False and rows[0].downloadable is True
        assert visited == [ROOT_URL, BASE + "/index.php/feed/all"]
        assert len(s.last_search_warnings) == 1
        assert "partial_results" in s.last_search_warnings[0]
        assert "sous-catalogues" in s.last_search_warnings[0]
    finally:
        s.close()


def test_pdf_only_is_real_catalogue_notice_but_not_epub_acquisition():
    s, visited = session(feed(branch()), all_books=feed(PDF))
    try:
        rows = s.search("")
        assert len(rows) == 1 and rows[0].title == "Notice uniquement PDF"
        assert rows[0].owned is False
        assert rows[0].downloadable is False
        assert rows[0].download_url is None
        assert rows[0].format is None
        assert visited == [ROOT_URL, BASE + "/index.php/feed/all"]
        assert s.last_search_warnings == []
    finally:
        s.close()


def test_valid_acquisition_with_navigation_same_feed_not_a_second_http_scan():
    # The root itself lists a real acquisition and unrelated subsection.
    # No automatic navigation into that subsection; honest partial coverage.
    s, visited = session(feed(BOOK + NAVIGATION))
    try:
        rows = s.search("")
        assert [r.title for r in rows] == ["Livre vraiment public"]
        assert len(visited) == 2  # Root + same root acquisition reread (legacy).
        assert all(u == ROOT_URL for u in visited)
        assert any("partial_results" in w for w in s.last_search_warnings)
    finally:
        s.close()
