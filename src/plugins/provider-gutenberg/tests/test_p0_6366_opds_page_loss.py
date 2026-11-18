"""#6366/#1917: discarded Gutenberg entries cannot attest a complete page."""
import httpx
import pytest

from cifereca_gutenberg import GutenbergSession

WARNING = "partial_results : entrées OPDS non qualifiées"
GOOD = """<entry><id>https://www.gutenberg.org/ebooks/42</id>
<title>Example</title><link rel="alternate" type="text/html" href="/ebooks/42"/>
<link rel="http://opds-spec.org/acquisition" type="application/epub+zip"
href="/ebooks/42.epub3.images"/></entry>"""
BAD = "<entry><title>Missing acquisition</title></entry>"
NO_ID = """<entry><title>Missing identity</title>
<link rel="http://opds-spec.org/acquisition" type="application/epub+zip"
href="/unknown.epub"/></entry>"""
PLACEHOLDER = "<entry><title>No records found.</title></entry>"


@pytest.mark.parametrize("entries,count,partial", [
    ("", 0, False),
    (PLACEHOLDER, 0, False),
    (GOOD, 1, False),
    (BAD, 0, True),
    (NO_ID, 0, True),
    (GOOD + BAD, 1, True),
    (GOOD + PLACEHOLDER, 1, True),
    (PLACEHOLDER + PLACEHOLDER, 0, True),
    (GOOD + GOOD, 1, False),
])
def test_opds_page_loss_is_explicit(entries, count, partial):
    calls = []
    content = ('<feed xmlns="http://www.w3.org/2005/Atom">' + entries + '</feed>').encode()
    session = GutenbergSession(
        credential_id="public", label="", credentials={},
        options={"contact": "tests@example.test"}, config={}, diagnostics=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(
        lambda req: (calls.append(req) or httpx.Response(200, content=content))
    ))
    try:
        assert len(session.search("Example")) == count
        assert session.last_search_warnings == ([WARNING] if partial else [])
        assert len(calls) == 1
    finally:
        session.close()
