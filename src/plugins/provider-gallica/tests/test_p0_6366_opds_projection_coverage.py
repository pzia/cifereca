"""#6366/#1917: an unqualified nonempty OPDS page is never authoritative zero."""
import httpx
import pytest

from cifereca_gallica import GallicaSession

GOOD = """<entry>
    <id>https://gallica.bnf.fr/ark:/12148/bpt6k5493831p</id>
    <title>Book</title>
    <link rel="http://opds-spec.org/acquisition/open-access"
          href="https://gallica.bnf.fr/ark:/12148/bpt6k5493831p.epub"
          type="application/epub+zip"/>
</entry>"""
NO_ID = GOOD.replace("<id>https://gallica.bnf.fr/ark:/12148/bpt6k5493831p</id>", "")
NO_ACQUISITION = """<entry>
    <id>https://gallica.bnf.fr/ark:/12148/bpt6k1279113</id>
    <title>No acquisition</title>
</entry>"""


class Diagnostics:
    def add(self, *args, **kwargs):
        pass


@pytest.mark.parametrize("entries,expected,partial", [
    ("", 0, False),
    (GOOD, 1, False),
    (NO_ID, 0, True),
    (NO_ACQUISITION, 0, True),
    (GOOD + NO_ID + NO_ACQUISITION, 1, True),
])
def test_opds_projection_coverage_through_public_search(entries, expected, partial):
    calls = []
    payload = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title>'
               + entries + '</feed>').encode()

    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=payload)

    session = GallicaSession(
        credential_id="gallica-public", label="Gallica", credentials={},
        options={"include_audio": False}, config={}, diagnostics=Diagnostics(),
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(respond))
    session.connect()
    try:
        # Field-specific search skips discovery; exactly one established OPDS GET.
        rows = session.search("Doyle", scope="digital", field="title")
        assert len(rows) == expected
        assert session.last_search_warnings == (
            ["partial_results : Gallica : certaines entrées OPDS non qualifiées"]
            if partial else []
        )
        assert len(calls) == 1
        assert all(row.record_id == "ark:/12148/bpt6k5493831p" for row in rows)

        # A subsequent valid empty call may not inherit prior partial status.
        payload = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Empty</title></feed>'
        assert session.search("Doyle", scope="digital", field="title") == []
        assert session.last_search_warnings == []
        assert len(calls) == 2
    finally:
        session.close()
