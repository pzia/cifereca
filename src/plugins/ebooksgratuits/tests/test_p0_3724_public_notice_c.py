"""#3724: exact anonymous COPS OPDS occurrence, synthetic HTTP only."""
from types import SimpleNamespace

import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession
from cifereca.core.search_orchestration import _public_rows_from_response, _mirror_remote_results
from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage

from test_opds_calibre_1084 import BOOK, NO_EPUB, HOST, route


def make_session(entries=BOOK, *, credential="__public__", credentials=None, options=None):
    calls = []
    def handler(req):
        calls.append(req.url.path)
        return route(req, entries=entries)
    session = EbooksSession(
        credential_id=credential, label="Public", credentials=credentials or {},
        options=options or {}, config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    session.connect()
    return session, calls


def test_same_call_public_occurrence_persists_without_legacy_delivery(tmp_path):
    session, calls = make_session()
    path = tmp_path / "state.sqlite"
    db = Storage(path)
    try:
        assert session.accounts == []
        response = session.search_with_response_facets("Livre", "catalog", content_types=("epub",))
        assert response.partial is False and response.catalogue_complete is False
        raw, = response.results
        notice, = response.public_notices
        assert notice.result is raw and raw.source_collection == "public_catalog"
        assert raw.friendly_url == HOST + "/index.php/book/42"
        assert raw.owned is raw.downloadable is False
        assert raw.download_url is raw.thumbnail_url is raw.description is None
        assert len(calls) == 3
        row, = _public_rows_from_response(response, session)
        assert _mirror_remote_results(SimpleNamespace(storage=db), [row],
                                      identity_id="alice", require_public_cover_witness=True) == 1
        exact = row._core_notice_cover_document_ref
        canonical = row._core_notice_canonical_cover_document_ref
        saved = read_notice_cover(db, exact)
        consolidated = read_consolidated_notice_cover(db, canonical)
        assert consolidated.sources[0].public_bibliography.title == raw.title
        assert evict_notice_cover(db, exact, expected_epoch=saved.epoch)
        offer, = session.search("Livre")
        assert offer.record_id == raw.record_id and offer.downloadable is True
        assert offer.download_url == HOST + "/index.php/fetch/42.epub"
        assert len(calls) == 6
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        session.close()
        db.close()
    db = Storage(path)
    try:
        hit, = search_public_notice_c(db, "Livre", group_by_document=True).hits
        assert hit.document == canonical and hit.record_id == raw.record_id
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


@pytest.mark.parametrize("entries", [
    BOOK + BOOK,
    BOOK + BOOK.replace("<title>Livre d'essai</title>", ""),
    BOOK.replace("<id>urn:uuid:42</id>", "<id>urn:uuid:42</id><id>urn:uuid:43</id>"),
    BOOK.replace("<id>urn:uuid:42</id>", ""),
    BOOK.replace("/fetch/42.epub", "/fetch/43.epub"),
    BOOK.replace("/book/42", "/book/43"),
    BOOK.replace("/fetch/42.epub", "/fetch/42.epub?token=fixture"),
    NO_EPUB,
])
def test_unqualified_occurrence_is_partial(entries):
    session, calls = make_session(entries)
    try:
        response = session.search_with_response_facets("Livre", "catalog")
        assert response.partial is True and response.results == []
        assert response.public_notices == () and len(calls) == 3
    finally:
        session.close()


@pytest.mark.parametrize("kwargs", [
    {"credential": "private"},
    {"credentials": {"secret": "fixture"}},
    {"options": {"opds_feed_url": "https://example.invalid/index.php/feed"}},
    {"options": {"acquisition_backend": "html"}},
])
def test_nonanonymous_or_custom_backend_does_not_query(kwargs):
    session, calls = make_session(**kwargs)
    try:
        response = session.search_with_response_facets("Livre", "catalog")
        assert response.partial is True and response.results == []
        assert response.public_notices == () and calls == []
    finally:
        session.close()


@pytest.mark.parametrize("types,count,partial", [
    (("epub",), 1, False),
    (("ebook",), 1, False),
    (("epub", "pdf"), 1, True),
    (("pdf",), 0, True),
])
def test_core_format_filter_uses_only_loaded_qualified_page(types, count, partial):
    session, calls = make_session()
    try:
        response = session.search_with_response_facets("Livre", "catalog", content_types=types)
        assert len(response.results) == len(response.public_notices) == count
        assert response.partial is partial and response.catalogue_complete is False
        assert len(calls) == 3 * count
    finally:
        session.close()
