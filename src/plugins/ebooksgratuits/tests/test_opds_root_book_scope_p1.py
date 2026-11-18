"""#1917: root browse must identify the same OPDS book entries as search.

Synthetic root feeds over MockTransport; no live requests or downloads.
"""
import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession, _safe_opds_link

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
        assert visited == ["/index.php/feed"]
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
        assert visited == ["/index.php/feed"]
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


@pytest.mark.parametrize("href", [
    "/index.php/account/private.epub",
    "/index.php/account/delete.epub",
    "/index.php/fetch-profile/42.epub",
    "/index.php/other/fetch/42.epub",
    "/admin/fetch.php",
])
def test_book_offer_must_use_an_attested_fetch_route(href):
    assert _safe_opds_link(HOST + "/index.php/feed", href, "download") is None


@pytest.mark.parametrize("href", [
    "/index.php/fetch/42.epub",
    "/index.php/fetch/42",
    "/fetch.php?id=42",
])
def test_documented_fetch_routes_remain_available(href):
    assert _safe_opds_link(HOST + "/index.php/feed", href, "download") == HOST + href


def test_root_browse_reuses_one_parse_without_cache_across_searches(monkeypatch):
    import cifereca_ebooksgratuits as adapter

    parsed = []
    real = adapter.parse_bounded_catalogue_xml

    def counted(body, **limits):
        parsed.append(body)
        return real(body, **limits)

    monkeypatch.setattr(adapter, "parse_bounded_catalogue_xml", counted)
    body = feed(entry("epub", href="/index.php/fetch/epub.epub"))
    obj, visited = session_with_root(body)
    try:
        for _ in range(2):
            rows = obj.search("")
            assert [x.title for x in rows] == ["Book epub"]
            assert not rows[0].owned
        assert visited == ["/index.php/feed", "/index.php/feed"]
        assert parsed == [body.encode(), body.encode()]
    finally:
        obj.close()


def test_root_browse_next_page_is_read_once_without_rereading_root(monkeypatch):
    import cifereca_ebooksgratuits as adapter

    parsed = []
    real = adapter.parse_bounded_catalogue_xml

    def counted(body, **limits):
        parsed.append(body)
        return real(body, **limits)

    monkeypatch.setattr(adapter, "parse_bounded_catalogue_xml", counted)
    first = feed(entry("one", href="/index.php/fetch/one.epub"),
                 '<link rel="next" href="/index.php/feed/page2"/>')
    second = feed(entry("two", href="/index.php/fetch/two.epub"))
    obj, visited = session_with_root(first, children={"/index.php/feed/page2": second})
    try:
        rows = obj.search("", page=1)
        assert [x.title for x in rows] == ["Book two"]
        assert visited == ["/index.php/feed", "/index.php/feed/page2"]
        assert parsed == [first.encode(), second.encode()]
    finally:
        obj.close()
