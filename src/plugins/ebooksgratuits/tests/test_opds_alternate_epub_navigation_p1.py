"""#1917: OPDS navigation must recognize the same EPUB links as item projection.

Synthetic Atom feeds via MockTransport: no live HTTP, account, or acquisition.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_ebooksgratuits import EbooksSession

HOST = "https://opds2.ebooksgratuits.org"
ATOM = "http://www.w3.org/2005/Atom"
ROOT = f"""<feed xmlns="{ATOM}">
<link rel="search" type="application/atom+xml"
href="/index.php/feed/search?q={{searchTerms}}"/>
</feed>"""


def feed(entry, next_url=None):
    continuation = (
        f'<link rel="next" href="{next_url}"/>' if next_url else ""
    )
    return f'<feed xmlns="{ATOM}">{entry}{continuation}</feed>'


def entry(*, href="/index.php/fetch/42.epub", rel="alternate",
          mime="application/epub+zip", identifier="42"):
    return f"""<entry>
<id>urn:book:{identifier}</id><title>Notice {identifier}</title>
<author><name>Autrice Exemple</name></author>
<link rel="{rel}" type="{mime}" href="{href}"/>
</entry>"""


def session_with_pages(pages):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        path = request.url.path
        if path == "/index.php/feed":
            body = ROOT
        elif path == "/index.php/feed/search":
            body = pages[0]
        elif path == "/index.php/feed/page2":
            body = pages[1]
        else:
            pytest.fail("Unexpected OPDS URL: " + path)
        return httpx.Response(200, text=body)

    session = EbooksSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    session.connect()
    return session, seen


def test_alternate_epub_is_recognized_as_a_book_not_a_navigation_scope():
    session, visited = session_with_pages([feed(entry())])
    try:
        rows = session.search("Notice")
        assert len(rows) == 1
        assert rows[0].title == "Notice 42"
        assert rows[0].download_url == HOST + "/index.php/fetch/42.epub"
        assert rows[0].owned is False
        assert rows[0].downloadable is True
        assert len(visited) == 2
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_alternate_epub_on_second_page_respects_next_and_page_boundary():
    next_url = "/index.php/feed/page2"
    session, visited = session_with_pages([
        feed(entry(identifier="1"), next_url=next_url),
        feed(entry(identifier="2", href="/index.php/fetch/2.epub")),
    ])
    try:
        rows = session.search("Notice", page=1)
        assert [row.title for row in rows] == ["Notice 2"]
        assert visited[-1] == HOST + next_url
        assert len(visited) == 3
    finally:
        session.close()


@pytest.mark.parametrize("href,mime", [
    ("https://evil.example/fetch/private.epub", "application/epub+zip"),
    ("/index.php/book/42", "text/html"),
    ("/index.php/account/private", "application/epub+zip"),
])
def test_unverified_alternate_is_not_treated_as_a_book(href, mime):
    session, visited = session_with_pages([feed(entry(href=href, mime=mime))])
    try:
        with pytest.raises(ProviderCapabilityError, match="browse_route_unavailable"):
            session.search("Notice")
        assert len(visited) == 2
        assert not any("evil.example" in url for url in visited)
    finally:
        session.close()
