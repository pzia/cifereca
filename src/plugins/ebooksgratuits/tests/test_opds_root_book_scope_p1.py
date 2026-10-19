"""#1917: root browse must identify the same OPDS book entries as search.

Synthetic root feeds over MockTransport; no live requests or downloads.
"""
import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession

HOST = "https://opds2.ebooksgratuits.org"
ATOM = "http://www.w3.org/2005/Atom"


def entry(book_id, *, href, relation="alternate", mime="application/epub+zip"):
    return (
        f'<entry><id>urn:book:{book_id}</id><title>Book {book_id}</title>'
        f'<link rel="{relation}" type="{mime}" href="{href}"/></entry>'
    )


def feed(*entries):
    return f'<feed xmlns="{ATOM}">' + ''.join(entries) + '</feed>'


def session_with_root(root, *, children=None):
    visited = []
    children = children or {}

    def handler(request):
        visited.append(request.url.path)
        if request.url.path == "/index.php/feed":
            body = root
        else:
            assert request.url.path in children, "Unverified navigation request"
            body = children[request.url.path]
        return httpx.Response(200, text=body)

    session = EbooksSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False,
    )
    session.connect()
    return session, visited


def test_root_alternate_epub_books_are_not_mistaken_for_navigation():
    root = feed(entry("epub", href="/index.php/fetch/epub.epub"))
    obj, visited = session_with_root(root)
    try:
        rows = obj.search("")
        assert [x.title for x in rows] == ["Book epub"]
        assert rows[0].downloadable is True
        assert rows[0].owned is False
        assert rows[0].download_url == HOST + "/index.php/fetch/epub.epub"
        assert visited == ["/index.php/feed", "/index.php/feed"]
    finally:
        obj.close()


def test_root_pdf_only_acquisition_is_a_real_non_downloadable_notice():
    root = feed(entry(
        "pdf", href="/index.php/fetch/pdf.pdf",
        relation="http://opds-spec.org/acquisition/open-access",
        mime="application/pdf",
    ))
    obj, visited = session_with_root(root)
    try:
        rows = obj.search("")
        assert [x.title for x in rows] == ["Book pdf"]
        assert rows[0].downloadable is False
        assert rows[0].download_url is None
        assert visited == ["/index.php/feed", "/index.php/feed"]
    finally:
        obj.close()


@pytest.mark.parametrize("href", (
    "https://evil.invalid/index.php/fetch/secret.epub",
    "/index.php/account/secret.epub",
    "https://opds2.ebooksgratuits.org:8443/index.php/fetch/a.epub",
))
def test_unverified_root_alternate_cannot_hide_valid_browse_branch(href):
    root = feed(
        entry("external", href=href),
        '<entry><id>urn:section</id><title>All books</title>'
        '<link rel="subsection" type="application/atom+xml"'
        ' href="/index.php/feed/all"/></entry>',
    )
    children = {
        "/index.php/feed/all": feed(entry(
            "valid", href="/index.php/fetch/valid.epub",
        )),
    }
    obj, visited = session_with_root(root, children=children)
    try:
        rows = obj.search("")
        assert [x.title for x in rows] == ["Book valid"]
        assert "evil.invalid" not in str(visited)
        assert visited == ["/index.php/feed", "/index.php/feed/all"]
    finally:
        obj.close()
