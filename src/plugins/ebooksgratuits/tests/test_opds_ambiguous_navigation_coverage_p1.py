"""#1917: split COPS/OPDS branches must not advertise exhaustive results.

Each URL is handled by MockTransport. No real catalogue, identity or download.
"""
import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession

HOST = "https://opds2.ebooksgratuits.org"
ATOM = "http://www.w3.org/2005/Atom"
WARNING = (
    "partial_results : plusieurs branches OPDS possibles ; "
    "une seule interrogée"
)
ROOT = f"""<feed xmlns="{ATOM}">
<link rel="search" type="application/atom+xml"
href="/index.php/feed/search?q={{searchTerms}}"/>
</feed>"""
BOOK = f"""<entry xmlns="{ATOM}">
<id>urn:book:1</id><title>Notice publique</title>
<author><name>Autrice</name></author>
<link rel="http://opds-spec.org/acquisition/open-access"
type="application/epub+zip" href="/index.php/fetch/1.epub"/>
</entry>"""


def nav(*links):
    return (
        f'<feed xmlns="{ATOM}">'
        + ''.join(
            '<entry><id>scope:book</id><title>Books</title>'
            '<link type="application/atom+xml" href="' + href + '"/>'
            '</entry>' for href in links
        )
        + "</feed>"
    )


def session_with_routes(routes):
    seen = []
    def request(req):
        seen.append(req.url.path)
        path = req.url.path
        if path not in routes:
            raise AssertionError("unexpected branch fetched: " + path)
        return httpx.Response(200, text=routes[path])
    obj = EbooksSession(
        credential_id="public", label="", credentials={},
        options={}, config={}, diagnostics=None, transport=None,
    )
    obj.client.close()
    obj.client = httpx.Client(transport=httpx.MockTransport(request))
    obj.connect()
    return obj, seen


def test_multiple_equal_priority_scopes_are_explicitly_partial():
    routes = {
        "/index.php/feed": ROOT,
        "/index.php/feed/search": nav("/index.php/feed/books1", "/index.php/feed/books2"),
        "/index.php/feed/books1": f'<feed xmlns="{ATOM}">{BOOK}</feed>',
    }
    obj, seen = session_with_routes(routes)
    try:
        rows = obj.search("Test")
        assert [row.title for row in rows] == ["Notice publique"]
        assert obj.last_search_warnings == [WARNING]
        assert seen == [
            "/index.php/feed", "/index.php/feed/search",
            "/index.php/feed/books1",
        ]
        # Never fabricate union and never follow the second branch.
        assert "/index.php/feed/books2" not in seen
    finally:
        obj.close()


def test_single_and_duplicate_same_url_scope_are_not_partial():
    for links in [
        ("/index.php/feed/books1",),
        ("/index.php/feed/books1", "/index.php/feed/books1"),
    ]:
        routes = {
            "/index.php/feed": ROOT,
            "/index.php/feed/search": nav(*links),
            "/index.php/feed/books1": f'<feed xmlns="{ATOM}">{BOOK}</feed>',
        }
        obj, seen = session_with_routes(routes)
        try:
            assert len(obj.search("Test")) == 1
            assert obj.last_search_warnings == []
        finally:
            obj.close()


def test_second_search_clears_prior_partial_warning():
    state = {"multiple": True}
    routes = {
        "/index.php/feed": ROOT,
        "/index.php/feed/books1": f'<feed xmlns="{ATOM}">{BOOK}</feed>',
    }
    seen = []
    def request(req):
        seen.append(req.url.path)
        if req.url.path == "/index.php/feed/search":
            hrefs = ("/index.php/feed/books1", "/index.php/feed/books2") if state["multiple"] else ("/index.php/feed/books1",)
            return httpx.Response(200, text=nav(*hrefs))
        return httpx.Response(200, text=routes[req.url.path])
    obj = EbooksSession(
        credential_id="public", label="", credentials={},
        options={}, config={}, diagnostics=None, transport=None,
    )
    obj.client.close()
    obj.client = httpx.Client(transport=httpx.MockTransport(request))
    obj.connect()
    try:
        assert len(obj.search("Test")) == 1
        assert obj.last_search_warnings == [WARNING]
        state["multiple"] = False
        assert len(obj.search("Test")) == 1
        assert obj.last_search_warnings == []
    finally:
        obj.close()
