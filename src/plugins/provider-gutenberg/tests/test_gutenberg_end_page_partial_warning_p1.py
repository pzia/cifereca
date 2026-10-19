"""#1917: beyond-last-page local Gutenberg fields remain explicitly partial."""
import httpx

from cifereca_gutenberg import GutenbergSession

EMPTY = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
WARNING = "partial_results : filtre local limité à la page OPDS demandée"


def make_session():
    calls = []
    session = GutenbergSession(
        credential_id="public", label="", credentials={},
        options={"contact": "catalog-tests@example.test"},
        config={}, diagnostics=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(
        lambda req: (calls.append(req) or httpx.Response(200, content=EMPTY))
    ))
    return session, calls


def test_author_page_beyond_last_available_warns_not_corpus_zero():
    session, calls = make_session()
    try:
        assert session.search("Absent Writer", field="author", page=2) == []
        assert len(calls) == 1
        assert session.last_search_warnings == [WARNING]
        # A genuine native all search must clear the previous partial claim.
        assert session.search("Absent Writer", field="all") == []
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_all_scope_exhausted_page_has_no_spurious_postfilter_warning():
    session, calls = make_session()
    try:
        assert session.search("Absent Writer", field="all", page=1) == []
        assert len(calls) == 1
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_subject_exhausted_page_warns_even_with_no_rows():
    session, calls = make_session()
    try:
        assert session.search("History", field="subject", page=1) == []
        assert len(calls) == 1
        assert session.last_search_warnings == [WARNING]
    finally:
        session.close()
