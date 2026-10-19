"""#1084: COPS OPDS Atom adapter, offline fixtures only; no upstream traffic."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_ebooksgratuits import EbooksSession, _safe_opds_link

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


def make_session(handler):
    session = EbooksSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    session.connect()
    assert session.acquisition_backend == "opds"
    return session


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


def test_search_uses_discovered_opensearch_and_publishes_only_attested_fields():
    requests=[]
    def handle(req):
        requests.append(str(req.url))
        return route(req)
    session=make_session(handle)
    try:
        rows=session.search("Livre",field="all")
        assert len(rows)==1
        book=rows[0]
        assert book.title=="Livre d'essai" and book.author=="Auteur Exemple"
        assert book.publisher=="Partenaire BEQ"
        assert book.published_at is None  # OPDS updated is not work publication
        assert book.downloadable and book.format=="EPUB"
        assert book.download_url==HOST+"/index.php/fetch/42.epub"
        assert book.friendly_url==HOST+"/index.php/book/42"
        assert book.metadata["acquisition_source"]=="opds-calibre"
        assert book.metadata["categories"]==["Jeunesse"]
        assert book.metadata["genres"]==["Roman"]
        assert book.metadata["catalog_updated_at"]=="2026-10-18T09:00:00Z"
        assert "recommended_age" not in book.metadata
        assert "territorial_rights_warning" not in book.metadata
        assert len(requests)==3
        assert "q=Livre" in requests[-1]
        assert all("ebooks.php" not in r for r in requests)
    finally:
        session.close()


def test_browse_uses_all_books_navigation_and_keeps_unsupported_offer():
    requests=[]
    def handler(req):
        requests.append(str(req.url))
        return route(req,entries=BOOK+NO_EPUB)
    session=make_session(handler)
    try:
        rows=session.search("")
        assert [r.title for r in rows]==["Livre d'essai","Notice sans EPUB"]
        assert rows[1].download_url is None and not rows[1].downloadable
        assert "available_formats" not in rows[1].metadata
        assert len(requests)==2
        assert requests[-1].endswith('/index.php/feed/all')
    finally:
        session.close()


def test_page_traversal_only_via_next_link():
    requests=[]
    def handle(req):
        requests.append(str(req.url))
        return route(req,next_page=True)
    session=make_session(handle)
    try:
        rows=session.search("livre",page=1)
        assert len(rows)==1 and rows[0].title=="Notice sans EPUB"
        assert len(requests)==4
        assert requests[-1].endswith('/index.php/feed/search?q=livre&p=1')
    finally:
        session.close()


def test_local_author_and_category_filter_is_explicitly_partial():
    session=make_session(route)
    try:
        rows=session.search("Exemple",field="author")
        assert len(rows)==1
        assert 'partial_results' in session.last_search_warnings[0]
        assert session.search("Jeunesse",field="category")[0].metadata["categories"]==["Jeunesse"]
        assert session.search("8",field="genre")==[]
        assert 'partial_results' in session.last_search_warnings[0]
    finally:
        session.close()


@pytest.mark.parametrize('page',[-1,True,101,'1',1.1])
def test_bad_page_never_reaches_network(page):
    session=make_session(lambda req: pytest.fail("no HTTP expected"))
    try:
        with pytest.raises(ProviderCapabilityError,match="invalid_page"):
            session.search("x",page=page)
    finally:
        session.close()


def test_blank_specialized_filter_does_not_reach_network():
    session=make_session(lambda req: pytest.fail("no HTTP expected"))
    try:
        assert session.search("  ",field="author")==[]
    finally:
        session.close()


@pytest.mark.parametrize("link", [
    "https://evil.example/index.php/fetch/2.epub",
    "//evil.example/index.php/fetch/2.epub",
    "http://opds2.ebooksgratuits.org/index.php/fetch/2.epub",
    "https://u:p@opds2.ebooksgratuits.org/index.php/fetch/2.epub",
    "https://opds2.ebooksgratuits.org:8443/index.php/fetch/2.epub",
    "javascript:alert(1)",
    "/index.php/account/delete",
    "/index.php/fetch/42.epub#fragment",
    "/index.php/fetch/42.epub%0a",
])
def test_untrusted_acquisition_links_fail_closed(link):
    assert _safe_opds_link(FEED,link,'download') is None


def test_hostile_opensearch_template_fails_without_second_origin_fetch():
    visited=[]
    root=ROOT.replace('/index.php/opensearch.xml','https://evil.example/search.xml')
    def handle(req):
        visited.append(str(req.url))
        return response(req,body=root)
    session=make_session(handle)
    try:
        with pytest.raises(ProviderCapabilityError,match='search_route_unavailable'):
            session.search('un livre')
        assert visited==[FEED]
    finally:
        session.close()


def test_invalid_html_and_dtd_never_become_empty_success():
    for payload in ('<html>challenge</html>', '<!DOCTYPE feed [<!ENTITY xx "test">]><feed/>'):
        session=make_session(lambda req: response(req,body=payload))
        try:
            with pytest.raises(ProviderError,match='invalid_opds_feed'):
                session.search('book')
        finally:
            session.close()


@pytest.mark.parametrize('status,code',[(401,'auth_required'),(403,'access_denied'),(429,'rate_limited'),(500,'service_unavailable')])
def test_failures_are_bounded_without_leaking_provider_body(status,code):
    session=make_session(lambda req: response(req,status,body='secret=PRIVATE'))
    try:
        with pytest.raises(ProviderError,match=code) as exc:
            session.search('book')
        assert 'PRIVATE' not in str(exc.value)
    finally:
        session.close()
