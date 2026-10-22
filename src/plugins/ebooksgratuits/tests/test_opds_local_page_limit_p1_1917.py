"""An oversized server page is partial, even without more OPDS branches."""
import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession


ATOM = "http://www.w3.org/2005/Atom"
WARNING = ('partial_results : page OPDS tronquée par la limite locale ; '
           'des notices de cette page ne sont pas affichées')


def book(index, author="Autrice"):
    return (f'<entry><id>urn:synthetic:{index}</id><title>Livre {index}</title>'
            f'<author><name>{author}</name></author>'
            '<link rel="http://opds-spec.org/acquisition/open-access" '
            f'type="application/epub+zip" href="/index.php/fetch/{index}.epub"/>'
            '</entry>')


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_warns_only_when_matching_books_are_actually_omitted(count):
    seen, state = [], {"count": count}

    def transport(request):
        seen.append(request.url.path)
        if request.url.path == "/index.php/feed":
            text = '<link rel="search" type="application/atom+xml" href="/index.php/feed/search?q={searchTerms}"/>'
        elif request.url.path == "/index.php/feed/search":
            text = ''.join(book(i) for i in range(state["count"]))
        else:
            pytest.fail("No detail, next-feed or acquisition request allowed")
        return httpx.Response(200, text=f'<feed xmlns="{ATOM}">{text}</feed>')

    session = EbooksSession(credential_id="public", label="", credentials={},
                           options={}, config={}, diagnostics=None, transport=None)
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(transport))
    session.page_size = 2
    session.connect()
    try:
        rows = session.search("Livre", field="all")
        assert len(rows) == min(count, 2)
        assert session.last_search_warnings == ([WARNING] if count > 2 else [])
        assert seen == ["/index.php/feed", "/index.php/feed/search"]
        assert all(row.downloadable and row.download_url for row in rows)
        # A later complete response must clear the earlier warning.
        state["count"] = 1
        session.search("Autre", field="all")
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_limit_is_checked_after_author_filter_without_hiding_filter_warning(monkeypatch):
    import cifereca_ebooksgratuits as module
    from xml.etree.ElementTree import fromstring

    feed = fromstring(f'<feed xmlns="{ATOM}">' + book(1) + book(2, "Autre") + book(3, "Autre") + '</feed>')
    root = fromstring(f'<feed xmlns="{ATOM}"><link rel="search" type="application/atom+xml" href="/index.php/feed/search?q={{searchTerms}}"/></feed>')
    monkeypatch.setattr(module, "_opds_fetch", lambda *_: (root, "https://opds2.ebooksgratuits.org/index.php/feed"))
    monkeypatch.setattr(module, "_opds_load_acquisition", lambda *_: (feed, "https://opds2.ebooksgratuits.org/index.php/feed/search"))
    session = EbooksSession(credential_id="public", label="", credentials={},
                           options={}, config={}, diagnostics=None, transport=None)
    session.page_size = 2
    session.connect()
    try:
        assert len(session.search("Autrice", field="author")) == 1
        assert WARNING not in session.last_search_warnings
        assert any("filtre author" in warning for warning in session.last_search_warnings)
    finally:
        session.close()
