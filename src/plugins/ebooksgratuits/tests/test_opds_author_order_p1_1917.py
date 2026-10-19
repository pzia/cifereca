"""#1917 P1 — explicit, limited Atom author matching on a loaded OPDS page.

All feeds are synthetic, served only via httpx.MockTransport. Search is
not exhaustive and never silently requests unrelated catalogue branches.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession, _opds_author_match

HOST = "https://opds2.ebooksgratuits.org"
ROOT = HOST + "/index.php/feed"
ROOT_XML = """<feed xmlns="http://www.w3.org/2005/Atom">
<link rel="search" type="application/opensearchdescription+xml"
 href="/index.php/opensearch.xml"/></feed>"""
OSD = """<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
<Url type="application/atom+xml"
 template="/index.php/feed/search?q={searchTerms}"/></OpenSearchDescription>"""
BODY = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>urn:book:hugo-1</id><title>Un livre Hugo</title>
<author><name>HUGO, Victor</name></author>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="/index.php/fetch/hugo1.epub"/></entry>
<entry><id>urn:book:hugo-2</id><title>Un autre livre Hugo</title>
<author><name>Victor Hugo</name></author>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="/index.php/fetch/hugo2.epub"/></entry>
<entry><id>urn:book:zola</id><title>Émile Zola</title>
<author><name>ZOLA, Émile</name></author>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="/index.php/fetch/zola.epub"/></entry>
<entry><id>urn:book:twopersons</id><title>Deux co-auteurs</title>
<author><name>Victor Dumas</name></author>
<author><name>Alexandre Hugo</name></author>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="/index.php/fetch/two.epub"/></entry>
<entry><id>urn:book:publisher-only</id><title>Victor Hugo</title>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="/index.php/fetch/publisher.epub"/></entry>
</feed>"""


def session():
    requested = []
    def handler(req):
        requested.append((req.url.path, str(req.url)))
        if req.url.path == "/index.php/feed":
            xml = ROOT_XML
        elif req.url.path == "/index.php/opensearch.xml":
            xml = OSD
        elif req.url.path == "/index.php/feed/search":
            xml = BODY
        else:
            pytest.fail("unexpected OPDS route " + req.url.path)
        return httpx.Response(200, text=xml, request=req)

    s = EbooksSession(
        credential_id="public", label="Catalogue", credentials={},
        options={}, config={}, diagnostics=None, transport=None,
    )
    s.client.close()
    s.client = httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False,
    )
    s.connect()
    return s, requested


@pytest.mark.parametrize("query,expect", [
    ("Victor Hugo", {"Un livre Hugo", "Un autre livre Hugo"}),
    ("HUGO Victor", {"Un livre Hugo", "Un autre livre Hugo"}),
    ("Zola Emile", {"Émile Zola"}),
    ("Émile Zola", {"Émile Zola"}),
    ("Hugo", {"Un livre Hugo", "Un autre livre Hugo", "Deux co-auteurs"}),
    ("Victor Zola", set()),
    ("Dumas Hugo", set()),  # Never concatenate two independent authors.
])
def test_author_search_matches_only_attested_atom_authors_in_loaded_page(query, expect):
    s, requested = session()
    try:
        rows = s.search(query, field="author")
        assert {row.title for row in rows} == expect
        assert len(requested) == 3
        assert all(p in {
            "/index.php/feed", "/index.php/opensearch.xml",
            "/index.php/feed/search",
        } for p, _ in requested)
        assert all(not row.owned for row in rows)
        assert all(row.source_collection == "public" for row in rows)
        assert s.last_search_warnings
        assert any("partial_results" in warn and "author" in warn
                   for warn in s.last_search_warnings)
        if not rows:
            # A local mismatch is not a verified global catalogue zero.
            assert s.last_search_warnings
    finally:
        s.close()


def test_atom_names_preserved_individually_with_public_evidence():
    s, _ = session()
    try:
        rows = s.search("Victor", field="author")
        duo = next(row for row in rows if row.title == "Deux co-auteurs")
        assert duo.metadata["opds_author_names"] == [
            "Victor Dumas", "Alexandre Hugo",
        ]
        assert duo.metadata["opds_author_evidence"] == "opds.atom.author.name"
        assert duo.author == "Victor Dumas, Alexandre Hugo"
        assert duo.downloadable and duo.download_url.endswith("/two.epub")
        assert not duo.owned
    finally:
        s.close()


@pytest.mark.parametrize("query,names", [
    ("Victor Hugo", ["Victor Dumas", "Alexandre Hugo"]),
    ("Emile Zola", ["Émile Durand", "Paul Zola"]),
    ("Victor", []),
    ("Victor Hugo", "Victor Hugo"),
    ("Victor Hugo", [{"name": "Victor Hugo"}]),
    ("", ["Victor Hugo"]),
])
def test_no_cross_author_or_untyped_cache_matching(query,names):
    assert _opds_author_match(query,names) is False


@pytest.mark.parametrize("query,authors", [
    ("Hugo Victor", ["HUGO, Victor"]),
    ("ÉMILE ZOLA", ["Zola, Émile"]),
    ("Hugo", ["Hugo, Victor"]),
])
def test_name_normalization_is_case_accent_and_order_safe(query,authors):
    assert _opds_author_match(query,authors) is True
