"""#3724: exact anonymous COPS OPDS occurrence, synthetic HTTP only."""
from types import SimpleNamespace

import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession
from cifereca.core.search_orchestration import _public_rows_from_response, _mirror_remote_results
from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage

from urllib.parse import parse_qs

# Same wire shapes as the legacy fixture; self-contained under pytest importlib mode.
HOST = "https://opds2.ebooksgratuits.org"
FEED = HOST + "/index.php/feed"
ROOT = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Bibliothèque Calibre</title>
<link rel="search" type="application/opensearchdescription+xml"
 href="/index.php/opensearch.xml" />
<entry><id>urn:all</id><title>All books</title>
<link rel="subsection" type="application/atom+xml;profile=opds-catalog"
 href="/index.php/feed/all" /></entry>
<entry><id>urn:recent</id><title>Recent additions</title>
<link rel="subsection" type="application/atom+xml;profile=opds-catalog"
 href="/index.php/feed/recent" /></entry>
</feed>"""
OSD = """<?xml version="1.0"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
<Url type="application/atom+xml;profile=opds-catalog"
 template="https://opds2.ebooksgratuits.org/index.php/feed/search?q={searchTerms}" />
</OpenSearchDescription>"""
BOOK = """<entry xmlns="http://www.w3.org/2005/Atom"
xmlns:dc="http://purl.org/dc/terms/">
<id>urn:uuid:42</id><title>Livre d'essai</title>
<author><name>Auteur Exemple</name></author>
<dc:publisher>Partenaire BEQ</dc:publisher>
<summary>Une histoire pour les jeunes</summary>
<updated>2026-10-18T09:00:00Z</updated>
<category term="Jeunesse" label="Jeunesse" />
<category term="Roman" label="Roman" scheme="https://example.org/genres" />
<link rel="alternate" type="text/html" href="/index.php/book/42"/>
<link rel="http://opds-spec.org/image/thumbnail" type="image/jpeg" href="/index.php/cover/42"/>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="/index.php/fetch/42.epub"/>
</entry>"""
NO_EPUB = """<entry xmlns="http://www.w3.org/2005/Atom">
<id>urn:uuid:43</id><title>Notice sans EPUB</title>
<author><name>Autrice B</name></author>
<link rel="alternate" type="text/html" href="/index.php/book/43" />
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/pdf" href="/index.php/fetch/43.pdf"/>
</entry>"""


def page(entries=BOOK, next_link=""):
    return ('<feed xmlns="http://www.w3.org/2005/Atom">' + entries
            + (f'<link rel="next" href="{next_link}"/>' if next_link else '')
            + '</feed>')


def response(req, status=200, body=ROOT, headers=None):
    return httpx.Response(status, text=body, headers=headers, request=req)


def route(req, *, entries=BOOK, search=True, navigation=True, next_page=False):
    path = req.url.path
    if path == "/index.php/feed":
        return response(req,body=ROOT)
    if path == "/index.php/opensearch.xml":
        return response(req,body=OSD)
    if path == "/index.php/feed/all":
        return response(req,body=page(entries))
    if path == "/index.php/feed/search":
        n = parse_qs(req.url.query.decode()).get('p', ['0'])[0]
        if n == "1":
            return response(req,body=page(NO_EPUB))
        return response(req,body=page(entries,'/index.php/feed/search?q=livre&amp;p=1' if next_page else ''))
    raise AssertionError("Unexpected request route " + path)


def make_session(entries=BOOK, *, credential="__public__", credentials=None, options=None):
    calls = []
    def handler(req):
        calls.append(req.url.path)
        return route(req, entries=entries)
    session = EbooksSession(
        credential_id=credential, label="Public", credentials=credentials or {},
        options=options or {}, config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    session.connect()
    return session, calls


def test_same_call_public_occurrence_persists_without_legacy_delivery(tmp_path):
    session, calls = make_session()
    path = tmp_path / "state.sqlite"
    db = Storage(path)
    try:
        assert session.accounts == []
        response = session.search_with_response_facets("Livre", "catalog", content_types=("epub",))
        assert response.partial is False and response.catalogue_complete is False
        raw, = response.results
        notice, = response.public_notices
        assert notice.result is raw and raw.source_collection == "public_catalog"
        assert raw.friendly_url == HOST + "/index.php/book/42"
        assert raw.owned is raw.downloadable is False
        assert raw.download_url is raw.thumbnail_url is raw.description is None
        assert len(calls) == 3
        row, = _public_rows_from_response(response, session)
        assert _mirror_remote_results(SimpleNamespace(storage=db), [row],
                                      identity_id="alice", require_public_cover_witness=True) == 1
        exact = row._core_notice_cover_document_ref
        canonical = row._core_notice_canonical_cover_document_ref
        saved = read_notice_cover(db, exact)
        consolidated = read_consolidated_notice_cover(db, canonical)
        assert consolidated.sources[0].public_bibliography.title == raw.title
        assert evict_notice_cover(db, exact, expected_epoch=saved.epoch)
        offer, = session.search("Livre")
        assert offer.record_id == raw.record_id and offer.downloadable is True
        assert offer.download_url == HOST + "/index.php/fetch/42.epub"
        assert len(calls) == 6
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        session.close()
        db.close()
    db = Storage(path)
    try:
        hit, = search_public_notice_c(db, "Livre", group_by_document=True).hits
        assert hit.document == canonical and hit.record_id == raw.record_id
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


@pytest.mark.parametrize("entries", [
    BOOK + BOOK,
    BOOK + BOOK.replace("<title>Livre d'essai</title>", ""),
    BOOK.replace("<id>urn:uuid:42</id>", "<id>urn:uuid:42</id><id>urn:uuid:43</id>"),
    BOOK.replace("<id>urn:uuid:42</id>", ""),
    BOOK.replace("/fetch/42.epub", "/fetch/43.epub"),
    BOOK.replace("/book/42", "/book/43"),
    BOOK.replace("/fetch/42.epub", "/fetch/42.epub?token=fixture"),
    NO_EPUB,
])
def test_unqualified_occurrence_is_partial(entries):
    session, calls = make_session(entries)
    try:
        response = session.search_with_response_facets("Livre", "catalog")
        assert response.partial is True and response.results == []
        assert response.public_notices == () and len(calls) == 3
    finally:
        session.close()


@pytest.mark.parametrize("kwargs", [
    {"credential": "private"},
    {"credentials": {"secret": "fixture"}},
    {"options": {"opds_feed_url": "https://example.invalid/index.php/feed"}},
    {"options": {"acquisition_backend": "html"}},
])
def test_nonanonymous_or_custom_backend_does_not_query(kwargs):
    session, calls = make_session(**kwargs)
    try:
        response = session.search_with_response_facets("Livre", "catalog")
        assert response.partial is True and response.results == []
        assert response.public_notices == () and calls == []
    finally:
        session.close()


@pytest.mark.parametrize("types,count,partial", [
    (("epub",), 1, False),
    (("ebook",), 1, False),
    (("epub", "pdf"), 1, True),
    (("pdf",), 0, True),
])
def test_core_format_filter_uses_only_loaded_qualified_page(types, count, partial):
    session, calls = make_session()
    try:
        response = session.search_with_response_facets("Livre", "catalog", content_types=types)
        assert len(response.results) == len(response.public_notices) == count
        assert response.partial is partial and response.catalogue_complete is False
        assert len(calls) == 3 * count
    finally:
        session.close()


@pytest.mark.parametrize("route", (
    "/search",
    "/Search.aspx",
    "/index.php/search",
    "/index.php/search.aspx",
    "/%73earch",
    "/index.php/%73earch",
))
def test_search_html_alternate_cannot_mint_a_book_web_reference(route):
    from cifereca_ebooksgratuits import _opds_public_html_link
    from cifereca.core.search_orchestration import _public_rows_from_response

    assert _opds_public_html_link(FEED, route) is None
    entries = BOOK.replace("/index.php/book/42", route)
    session, calls = make_session(entries)
    try:
        # A book still has an EPUB offer in legacy Search, but a search
        # navigation route cannot be surfaced as its web detail.
        item, = session.search("Livre")
        assert item.download_url == HOST + "/index.php/fetch/42.epub"
        assert item.friendly_url is None
        assert len(calls) == 3
        # In the durable C declaration every unqualified HTML alternate
        # refuses the whole entry, not just its web URL. Never claim exact
        # book-only evidence after a contradictory link declaration.
        public = session.search_with_response_facets("Livre", "catalog")
        assert public.partial is True and public.results == []
        assert public.public_notices == ()
        assert _public_rows_from_response(public, session) == []
        assert len(calls) == 6
    finally:
        session.close()


def test_search_alternate_does_not_override_an_independent_book_html_link():
    from cifereca_ebooksgratuits import _opds_public_html_link
    assert _opds_public_html_link(FEED, "/index.php/book/42") == (
        HOST + "/index.php/book/42"
    )
    extra_link = '<link rel="alternate" type="text/html" href="/index.php/search" />'
    session, calls = make_session(BOOK.replace("</entry>", extra_link + "</entry>"))
    try:
        item, = session.search("Livre")
        assert item.friendly_url == HOST + "/index.php/book/42"
        assert item.downloadable is True
        public = session.search_with_response_facets("Livre", "catalog")
        assert public.partial is True and public.public_notices == ()
        assert len(calls) == 6
    finally:
        session.close()
