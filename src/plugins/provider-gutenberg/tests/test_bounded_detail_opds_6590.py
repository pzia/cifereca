"""Bounded Gutenberg Atom detail, no network beyond the recorded GET."""
import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_gutenberg import GutenbergSession, MAX_SEARCH_OPDS_BYTES

BASE = "https://www.gutenberg.org/ebooks/42.opds"
LINK = '<link rel="http://opds-spec.org/acquisition" type="application/epub+zip" href="/ebooks/42.epub3.images"/>'
COVER = '<link rel="http://opds-spec.org/image/thumbnail" href="thumb.jpg"/>'


@pytest.mark.parametrize("root", ("feed", "entry"))
def test_atom_detail_keeps_epub_and_cover(root):
    body = (f'<{root} xmlns="http://www.w3.org/2005/Atom">' + LINK + COVER + f'</{root}>').encode()
    session = GutenbergSession.__new__(GutenbergSession)
    assert session._epub_candidates(body, BASE) == [("https://www.gutenberg.org/ebooks/42.epub3.images", "")]
    assert session._detail_cover(body, BASE) == "https://www.gutenberg.org/ebooks/thumb.jpg"


@pytest.mark.parametrize("body", (
    b"<html/>",
    ("<not_atom>" + LINK + COVER + "</not_atom>").encode(),
    ('<!DOCTYPE entry [<!ENTITY secret "PRIVATE_VALUE">]><entry xmlns="http://www.w3.org/2005/Atom"><title>&secret;</title>' + LINK + COVER + "</entry>").encode("utf-16"),
    ('<entry xmlns="http://www.w3.org/2005/Atom">' + "<x>" * 100 + "</x>" * 100 + LINK + COVER + "</entry>").encode(),
))
def test_invalid_detail_never_yields_links(body):
    session = GutenbergSession.__new__(GutenbergSession)
    assert session._detail_cover(body, BASE) is None
    with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
        session._epub_candidates(body, BASE)
    assert caught.value.__context__ is None
    assert "PRIVATE_VALUE" not in str(caught.value)


def test_oversized_detail_never_enters_cache():
    session = GutenbergSession(credential_id="public", label="", credentials={},
        options={"contact": "tests@example.test"}, config={}, diagnostics=None)
    session.client.close()
    calls = []
    def serve(request):
        calls.append(request)
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, content=b"x" * (MAX_SEARCH_OPDS_BYTES + 1))
    session.client = httpx.Client(transport=httpx.MockTransport(serve))
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed"):
            session._detail_feed(BASE)
        assert len(calls) == 1
        assert not session._detail_feed_cache
    finally:
        session.close()
