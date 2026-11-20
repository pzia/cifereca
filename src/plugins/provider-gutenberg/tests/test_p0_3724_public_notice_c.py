"""Gutenberg documentary envelope: actual OPDS parser and durable Core C.

All HTTP bodies are synthetic and no acquisition is executed.
"""
from types import SimpleNamespace

import httpx
import pytest

from cifereca_gutenberg import GutenbergSession, DEFAULT_OPDS_SEARCH_URL
from cifereca.core.search_orchestration import (
    _public_rows_from_response, _mirror_remote_results,
)
from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.storage import Storage


ENTRY = """<entry>
<id>http://www.gutenberg.org/ebooks/42</id><title>Exact OPDS book</title>
<author><name>Example Writer</name></author>
<dcterms:language>en</dcterms:language><dcterms:issued>2020</dcterms:issued>
<updated>2026-01-01T00:00:00Z</updated>
<rights>public claim not an entitlement</rights>
<link rel="alternate" type="text/html" href="/ebooks/42"/>
<link rel="http://opds-spec.org/acquisition" type="application/epub+zip"
 href="/ebooks/42.epub3.images"/>
<link rel="http://opds-spec.org/image" href="/unsafe-cover.jpg?token=secret"/>
</entry>"""


def make_session(entries=ENTRY, *, credential="__public__", credentials=None, options=None):
    calls = []
    content = ('<feed xmlns="http://www.w3.org/2005/Atom" '
               'xmlns:dcterms="http://purl.org/dc/terms/">' + entries + '</feed>').encode()
    obj = GutenbergSession(
        credential_id=credential, label="Gutenberg", credentials=credentials or {},
        options={"contact": "fixture@example.test", **(options or {})},
        config={}, diagnostics=None,
    )
    obj.client.close()
    obj.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: (calls.append(request) or httpx.Response(200, content=content))
    ))
    obj.connect()
    return obj, calls


def test_catalogue_envelope_same_call_persists_and_legacy_offer_remains(tmp_path, monkeypatch):
    session, calls = make_session()
    db = Storage(tmp_path / "state.sqlite")
    parses = []
    original = session._read_atom_feed

    def read(*args, **kwargs):
        parses.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(session, "_read_atom_feed", read)
    try:
        assert session.connected and session.accounts == []
        response = session.search_with_response_facets("Exact OPDS book", "catalog")
        assert len(calls) == len(parses) == 1
        assert response.partial is False and response.catalogue_complete is False
        raw, = response.results
        declaration, = response.public_notices
        assert declaration.result is raw
        assert raw.source_collection == "public_catalog"
        assert raw.downloadable is raw.owned is False
        assert raw.download_url is raw.thumbnail_url is raw.description is None
        assert raw.friendly_url == "https://www.gutenberg.org/ebooks/42"
        assert "rights" not in raw.metadata and "opds_detail_url" not in raw.metadata
        row, = _public_rows_from_response(response, session)
        assert _mirror_remote_results(SimpleNamespace(storage=db), [row],
                                      identity_id="alice", require_public_cover_witness=True) == 1
        exact = row._core_notice_cover_document_ref
        canonical = row._core_notice_canonical_cover_document_ref
        source = read_notice_cover(db, exact)
        assert source.attribute.state == "pending"
        c = read_consolidated_notice_cover(db, canonical)
        facts = c.sources[0].public_bibliography
        assert facts.title == "Exact OPDS book"
        assert facts.language_observation.languages == ("en",)
        assert evict_notice_cover(db, exact, expected_epoch=source.epoch)
        assert db.entitlements() == [] and db.acquisitions() == []
        # Separate explicit legacy search still returns the delivery offer;
        # it is not the documentary row and does not imply ownership.
        offer, = session.search("Exact OPDS book")
        assert offer.downloadable is True and offer.owned is False
        assert offer.download_url == "https://www.gutenberg.org/ebooks/42.epub3.images"
        assert session.acquisition_offers(offer)[0].source["url"] == offer.download_url
        assert len(calls) == len(parses) == 2
    finally:
        session.close()
        db.close()

    db = Storage(tmp_path / "state.sqlite")
    try:
        page = search_public_notice_c(db, "Exact OPDS book", group_by_document=True)
        known, = merge_public_notice_c_known_rows([], page, identity_id="bob")
        assert known["_cover_document_ref"] == canonical
        assert known["metadata"]["languages"] == ["en"]
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


@pytest.mark.parametrize("entries", [
    ENTRY + ENTRY,
    ENTRY.replace("<id>http://www.gutenberg.org/ebooks/42</id>", ""),
    ENTRY.replace("<title>Exact OPDS book</title>", ""),
    ENTRY.replace('href="/ebooks/42.epub3.images"', 'href="/ebooks/43.epub3.images"'),
])
def test_ambiguous_or_incomplete_record_is_not_a_public_occurrence(entries):
    session, calls = make_session(entries)
    try:
        response = session.search_with_response_facets("Exact OPDS book", "catalog")
        assert response.results == [] and response.public_notices == ()
        assert response.partial is True
        assert len(calls) == 1
    finally:
        session.close()


@pytest.mark.parametrize("kwargs", [
    {"credential": "private"},
    {"credentials": {"secret": "fixture"}},
    {"options": {"opds_search_url": "https://example.test/search"}},
])
def test_unqualified_session_does_not_search_under_public_contract(kwargs):
    session, calls = make_session(**kwargs)
    try:
        response = session.search_with_response_facets("Exact OPDS book", "catalog")
        assert response.results == [] and response.public_notices == ()
        assert response.partial is True and calls == []
    finally:
        session.close()


@pytest.mark.parametrize("extra", [
    '<link rel="alternate" type="text/html" href="/ebooks/43"/>',
    '<link rel="subsection" href="/ebooks/42.opds"/><link rel="subsection" href="/ebooks/43.opds"/>',
])
def test_secondary_coordinate_cannot_be_hidden_behind_first_link(extra):
    for entries in (ENTRY.replace("</entry>", extra + "</entry>"),
                    ENTRY.replace("<id>", extra + "<id>", 1)):
        session, calls = make_session(entries)
        try:
            response = session.search_with_response_facets("Exact OPDS book", "catalog")
            assert response.results == [] and response.public_notices == ()
            assert response.partial is True
            assert len(calls) == 1
        finally:
            session.close()


@pytest.mark.parametrize("extra,expected", [
    ("<dcterms:issued>2020</dcterms:issued>", "2020"),
    ("<dcterms:issued>2021</dcterms:issued>", None),
    ("<dcterms:issued>invalid</dcterms:issued>", None),
    ("<dcterms:issued><other>2020</other></dcterms:issued>", None),
])
def test_publication_date_requires_consistent_explicit_issued_fields(extra, expected):
    session, calls = make_session(ENTRY.replace("</entry>", extra + "</entry>"))
    try:
        response = session.search_with_response_facets("Exact OPDS book", "catalog")
        row, = response.results
        assert row.published_at == expected
        assert response.public_notices[0].result is row
        assert len(calls) == 1
        # Legacy offer semantics are deliberately unchanged.
        legacy, = session.search("Exact OPDS book")
        assert legacy.published_at == "2020"
        assert legacy.downloadable is True
    finally:
        session.close()


@pytest.mark.parametrize("extra,expected", [
    ('<link rel="alternate" type="text/html" href="/ebooks/42"/>',
     "https://www.gutenberg.org/ebooks/42"),
    ('<link rel="alternate" type="text/html" href="https://example.test/other"/>', None),
])
def test_web_fact_requires_unique_direct_atom_alternate(extra, expected):
    session, calls = make_session(ENTRY.replace("</entry>", extra + "</entry>"))
    try:
        response = session.search_with_response_facets("Exact OPDS book", "catalog")
        row, = response.results
        assert row.friendly_url == expected
        assert ("public_url_evidence" in row.metadata) is (expected is not None)
        assert row.download_url is None and row.downloadable is False
        assert len(calls) == 1
    finally:
        session.close()


@pytest.mark.parametrize("types,count,partial", [
    ((), 1, False),
    (("epub",), 1, False),
    (("EPUB",), 1, False),
    (("ebook",), 1, False),
    (("epub", "pdf"), 1, True),
    (("pdf",), 0, True),
    (("mp3",), 0, True),
])
def test_documentary_format_filter_accepts_core_ui_epub_family(types, count, partial):
    session, calls = make_session()
    try:
        response = session.search_with_response_facets(
            "Exact OPDS book", "catalog", content_types=types,
        )
        assert len(response.results) == len(response.public_notices) == count
        assert response.partial is partial
        assert response.catalogue_complete is False
        assert len(calls) == count
        assert bool(session.last_search_warnings) is partial
        if count:
            row, = response.results
            assert row.format == "EPUB"
            assert response.public_notices[0].result is row
            assert row.downloadable is False and row.download_url is None
        # A new supported call must not inherit earlier format warnings.
        again = session.search_with_response_facets("Exact OPDS book", "catalog",
                                                    content_types=("epub",))
        assert len(again.results) == 1 and again.partial is False
        assert session.last_search_warnings == []
        assert len(calls) == count + 1
    finally:
        session.close()


@pytest.mark.parametrize("types", [None, "epub", (None,), ("x" * 81,)])
def test_malformed_documentary_format_filter_does_not_query(types):
    from cifereca.plugin_api import ProviderCapabilityError

    session, calls = make_session()
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_content_type"):
            session.search_with_response_facets("Exact OPDS book", "catalog",
                                                content_types=types)
        assert calls == []
    finally:
        session.close()
