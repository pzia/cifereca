"""#1917: Gutenberg OPDS pagination never hides unverified rel=next as zero."""
from types import SimpleNamespace

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_gutenberg import GutenbergSession


BASE = "https://www.gutenberg.org/ebooks/search.opds/?query=foo"


def atom(link):
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom">' + link + '</feed>'
    ).encode()


@pytest.mark.parametrize("href", [
    "http://www.gutenberg.org/ebooks/search.opds/?query=foo&start_index=26",
    "https://www.gutenberg.org:444/ebooks/search.opds/?query=foo",
    "https://evil.example/ebooks/search.opds/?query=foo",
    "https://user:password@www.gutenberg.org/ebooks/search.opds/",
    "https://www.gutenberg.org/ebooks/search.opds/#fragment",
    " javascript:invalid ",
    "https://www.gutenberg.org/ebooks/search.opds/?query=foo\\private",
    "https://www.gutenberg.org/ebooks/search.opds/?query=foo&#10;private",
])
def test_insecure_or_changed_next_link_is_typed_error(href):
    # Only XML escaping is needed; no network or user-supplied credentials.
    import html
    content = atom('<link rel="next" href="' + html.escape(href, quote=True) + '"/>')
    with pytest.raises(ProviderError, match="opds_next_link_unverified") as err:
        GutenbergSession._next_page_url(content, BASE)
    assert href not in str(err.value)


def test_conflicting_next_links_are_not_silently_chosen():
    xml = atom(
        '<link rel="next" href="?query=foo&amp;start_index=26"/>'
        '<link rel="next" href="?query=foo&amp;start_index=51"/>'
    )
    with pytest.raises(ProviderError, match="opds_next_link_ambiguous"):
        GutenbergSession._next_page_url(xml, BASE)


def test_missing_link_is_legitimate_end_and_attested_relative_link_is_allowed():
    assert GutenbergSession._next_page_url(atom(""), BASE) is None
    relative = atom('<link rel="next" href="?query=foo&amp;start_index=26"/>')
    assert GutenbergSession._next_page_url(relative, BASE) == (
        "https://www.gutenberg.org/ebooks/search.opds/?query=foo&start_index=26"
    )


def test_invalid_next_never_claims_page_one_empty_before_second_get():
    seen = []

    def mock(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=atom(
            '<link rel="next" href="https://evil.example/private?token=SECRET"/>'
        ))

    session = GutenbergSession(
        credential_id="public", label="", credentials={},
        options={"contact": "tests@example.test"}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(mock))
    try:
        with pytest.raises(ProviderError, match="opds_next_link_unverified") as err:
            session.search("foo", page=1)
        assert len(seen) == 1
        assert "SECRET" not in str(err.value)
        assert session.last_search_warnings == []
    finally:
        session.close()
