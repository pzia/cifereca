"""#1917: browsing one COPS subsection must never claim global coverage.

Pure MockTransport OPDS Atom fixtures; no live HTTP or personal credentials.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession

FEED = "https://opds2.ebooksgratuits.org/index.php/feed"
BOOK = """<entry><id>urn:public:1</id><title>Un livre attesté</title>
<author><name>Auteur publié</name></author>
<link rel="http://opds-spec.org/acquisition/open-access"
 type="application/epub+zip" href="/index.php/fetch/1.epub"/></entry>"""


def atom(entries):
    return '<feed xmlns="http://www.w3.org/2005/Atom">' + entries + '</feed>'


def branch(title, url, identifier="urn:nav"):
    return (
        f'<entry><id>{identifier}</id><title>{title}</title>'
        f'<link rel="subsection" type="application/atom+xml"'
        f' href="/index.php/feed/{url}"/></entry>'
    )


def session(root, branch_pages):
    requests = []
    def handle(request):
        requests.append(request.url.path)
        path = request.url.path
        if path == "/index.php/feed":
            body = root
        else:
            assert path.startswith("/index.php/feed/")
            assert path in branch_pages, path
            body = branch_pages[path]
        return httpx.Response(200, text=body, request=request)

    value = EbooksSession(
        credential_id="__public__", label="Public", credentials={},
        options={}, config={}, diagnostics=None, transport=None,
    )
    value.client.close()
    value.client = httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    value.connect()
    return value, requests


def test_unique_all_books_branch_is_preferred_without_false_partial_warning():
    root = atom(branch("Recent additions", "recent", "urn:recent")
                + branch("All books", "all", "urn:all"))
    s, calls = session(root, {"/index.php/feed/all": atom(BOOK)})
    try:
        rows = s.search("")
        assert len(rows) == 1
        assert rows[0].owned is False and rows[0].downloadable is True
        assert calls == ["/index.php/feed", "/index.php/feed/all"]
        assert s.last_search_warnings == []
    finally:
        s.close()


def test_missing_all_books_route_marks_single_selected_subcatalogue_as_partial():
    root = atom(branch("Authors", "authors", "urn:authors")
                + branch("Recent additions", "recent", "urn:recent"))
    s, calls = session(root, {"/index.php/feed/authors": atom(BOOK)})
    try:
        rows = s.search("", field="all")
        assert len(rows) == 1
        assert calls == ["/index.php/feed", "/index.php/feed/authors"]
        assert len(s.last_search_warnings) == 1
        assert "partial_results" in s.last_search_warnings[0]
        assert "Tous les livres" in s.last_search_warnings[0]
    finally:
        s.close()


def test_ambiguous_all_books_feeds_mark_one_branch_only():
    root = atom(branch("All books / A", "all-a", "urn:a")
                + branch("All books / B", "all-b", "urn:b"))
    s, calls = session(root, {"/index.php/feed/all-a": atom(BOOK)})
    try:
        assert len(s.search("")) == 1
        assert calls == ["/index.php/feed", "/index.php/feed/all-a"]
        assert any("partial_results" in line for line in s.last_search_warnings)
    finally:
        s.close()


def test_empty_partial_branch_cannot_be_interpreted_as_full_catalogue_zero():
    root = atom(branch("Recent", "recent"))
    s, calls = session(root, {"/index.php/feed/recent": atom("")})
    try:
        assert s.search("") == []
        assert calls == ["/index.php/feed", "/index.php/feed/recent"]
        assert s.last_search_warnings and "partial_results" in s.last_search_warnings[0]
    finally:
        s.close()


def test_next_attempt_resets_previous_root_warning_after_route_failure():
    root = atom(branch("Recent", "recent"))
    s, calls = session(root, {"/index.php/feed/recent": atom("")})
    try:
        assert s.search("") == []
        assert s.last_search_warnings
        with pytest.raises(Exception):
            s.search("Recherche sans OpenSearch")
        assert s.last_search_warnings == []
    finally:
        s.close()
