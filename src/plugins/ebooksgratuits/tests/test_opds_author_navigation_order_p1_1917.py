"""#1917 P1: bounded author navigation on a declared COPS/OPDS branch.

Synthetic Atom via MockTransport. Selection of a published subsection does
not prove comprehensive author search or ownership; no account/download GET.
"""
from __future__ import annotations

import html

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_ebooksgratuits import (
    EbooksSession, _opds_author_navigation_match,
)

HOST = "https://opds2.ebooksgratuits.org"
ATOM = "http://www.w3.org/2005/Atom"
ROOT = (
    f'<feed xmlns="{ATOM}"><link rel="search" type="application/atom+xml"'
    ' href="/index.php/feed/search?q={searchTerms}"/></feed>'
)


def page(entries):
    return f'<feed xmlns="{ATOM}">' + entries + "</feed>"


def navigation(label, path):
    return (
        "<entry><id>urn:public:navigation</id><title>"
        + html.escape(label)
        + "</title><link rel=\"subsection\" type=\"application/atom+xml\" href=\""
        + html.escape(path, quote=True)
        + "\"/></entry>"
    )


def book(name="HUGO, Victor", book_id="42"):
    return (
        '<entry><id>urn:book:' + book_id + '</id><title>Une édition publique</title>'
        + "<author><name>" + html.escape(name) + "</name></author>"
        + '<link rel="http://opds-spec.org/acquisition/open-access"'
        + ' type="application/epub+zip" href="/index.php/fetch/' + book_id
        + '.epub"/></entry>'
    )


def session(search_feed, paths):
    visited = []

    def handle(request):
        key = request.url.path
        visited.append(key)
        if key == "/index.php/feed":
            response = ROOT
        elif key == "/index.php/feed/search":
            response = search_feed
        else:
            assert key in paths, f"unexpected OPDS route: {key}"
            response = paths[key]
        return httpx.Response(200, text=response, request=request)

    obj = EbooksSession(
        credential_id="public", label="Public", credentials={},
        options={}, config={}, diagnostics=None,
    )
    obj.client.close()
    obj.client = httpx.Client(
        transport=httpx.MockTransport(handle), trust_env=False,
    )
    obj.connect()
    return obj, visited


@pytest.mark.parametrize("query,label", [
    ("Victor Hugo", "HUGO, Victor"),
    ("HUGO Victor", "HUGO, Victor"),
    ("Émile Zola", "ZOLA, Émile"),
    ("Jean de La Fontaine", "LA FONTAINE, Jean de"),
])
def test_single_author_navigation_inverted_label_is_followed(query, label):
    root = page(
        navigation(label, "/index.php/feed/author-selected")
        + navigation("ZOLA, Alphonse", "/index.php/feed/another-author")
    )
    s, requests = session(root, {
        "/index.php/feed/author-selected": page(book(label))
    })
    try:
        result = s.search(query, field="author")
        assert len(result) == 1
        assert result[0].metadata["opds_author_evidence"] == "opds.atom.author.name"
        assert result[0].owned is False
        assert result[0].downloadable is True
        assert requests == [
            "/index.php/feed", "/index.php/feed/search",
            "/index.php/feed/author-selected",
        ]
        assert "partial_results" in s.last_search_warnings[0]
        assert "/index.php/feed/another-author" not in requests
    finally:
        s.close()


def test_two_matching_author_branches_are_explicitly_partial_without_union():
    root = page(
        navigation("HUGO, Victor", "/index.php/feed/hugo1")
        + navigation("Victor, Hugo", "/index.php/feed/hugo2")
    )
    s, requests = session(root, {
        "/index.php/feed/hugo1": page(book())
    })
    try:
        result = s.search("Victor Hugo", field="author")
        assert [row.title for row in result] == ["Une édition publique"]
        assert "/index.php/feed/hugo2" not in requests
        assert any("plusieurs branches OPDS possibles" in warning
                   for warning in s.last_search_warnings)
    finally:
        s.close()


@pytest.mark.parametrize("query,label", [
    ("Victor Hugo", "Victor Dumas & Alexandre Hugo"),
    ("Victor Hugo", "DUMAS, Victor; HUGO, Alexandre"),
    ("Victor Hugo", "HUGO, Victor, Alexandre"),
    ("Victor Hugo", "Books by HUGO, Victor"),
    ("Victor Hugo", "Auteur / HUGO, Victor"),
    ("Victor Hugo", "HUGO, Victor\\nAnother route"),
    ("Hugo Victor", "HUGO,"),
    ("Victor Hugo", "ZOLA, Émile"),
])
def test_route_without_single_author_label_is_not_a_permutation(query, label):
    assert _opds_author_navigation_match(query, label) is False


def test_unrelated_navigation_not_followed_and_cannot_claim_catalogue_zero():
    root = page(
        navigation("ZOLA, Émile", "/index.php/feed/zola")
        + navigation("DUMAS, Alexandre", "/index.php/feed/dumas")
    )
    s, requested = session(root, {})
    try:
        with pytest.raises(ProviderCapabilityError, match="browse_route_unavailable"):
            s.search("Victor Hugo", field="author")
        assert requested == ["/index.php/feed", "/index.php/feed/search"]
    finally:
        s.close()


def test_matching_navigation_label_does_not_make_unrelated_book_an_author_hit():
    root = page(navigation("HUGO, Victor", "/index.php/feed/hugo"))
    s, requested = session(root, {
        "/index.php/feed/hugo": page(book("DUMAS, Alexandre"))
    })
    try:
        assert s.search("Victor Hugo", field="author") == []
        assert any("partial_results" in msg for msg in s.last_search_warnings)
        assert requested == [
            "/index.php/feed", "/index.php/feed/search",
            "/index.php/feed/hugo",
        ]
    finally:
        s.close()


def test_external_navigation_url_is_never_followed():
    root = page(navigation("HUGO, Victor", "https://evil.invalid/account"))
    s, requested = session(root, {})
    try:
        with pytest.raises(ProviderCapabilityError, match="browse_route_unavailable"):
            s.search("Victor Hugo", field="author")
        assert requested == ["/index.php/feed", "/index.php/feed/search"]
    finally:
        s.close()
