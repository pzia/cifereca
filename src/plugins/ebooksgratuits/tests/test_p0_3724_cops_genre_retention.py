"""Explicit Atom genres survive documentary C retention, without rights."""
from contextlib import closing
from types import SimpleNamespace

import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover, read_notice_cover
from cifereca.core.search_orchestration import _mirror_remote_results, _public_rows_from_response
from cifereca.storage import Storage


@pytest.mark.parametrize("scheme, label, expected", [
    ("https://example.org/genres", "Astronomie", True),
    ("https://example.org/notgenre", "Astronomie", False),
    ("", "Astronomie", False),
    ("https://example.org/genres", "https://private.example/?token=hidden", False),
])
def test_opds_explicit_genre_is_retained_without_category_inference(tmp_path, scheme, label, expected):
    from xml.sax.saxutils import quoteattr

    host = "https://opds2.ebooksgratuits.org"
    feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>All books</title>'
            '<entry><id>urn:uuid:42</id><title>Roman témoin COPS</title>'
            '<author><name>Autrice témoin</name></author>'
            '<category term="Jeunesse" label="Jeunesse"/>'
            '<category scheme=' + quoteattr(scheme) + ' term=' + quoteattr(label) + '/>'
            '<link rel="alternate" type="text/html" href="/index.php/book/42"/>'
            '<link rel="http://opds-spec.org/acquisition/open-access" type="application/epub+zip"'
            ' href="/index.php/fetch/42.epub"/></entry></feed>')
    calls = []

    def transport(req):
        assert req.method == "GET"
        assert str(req.url) == host + "/index.php/feed"
        calls.append(req.url.path)
        return httpx.Response(200, text=feed, request=req)

    session = EbooksSession(credential_id="__public__", label="Public", credentials={},
                           options={}, config={}, diagnostics=None, transport=None)
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
    path = tmp_path / "state.sqlite"
    try:
        session.connect()
        response = session.search_with_response_facets("", "catalog")
        row, = response.results
        assert response.public_notices[0].result is row
        assert row.owned is row.downloadable is False and row.download_url is None
        assert row.metadata.get("genres") == (["Astronomie"] if expected else None)
        assert "categories" not in row.metadata and "age" not in row.metadata
        with closing(Storage(path)) as db:
            projected, = _public_rows_from_response(response, session)
            assert _mirror_remote_results(SimpleNamespace(storage=db), [projected],
                                          identity_id="alice", require_public_cover_witness=True) == 1
            canonical = projected._core_notice_canonical_cover_document_ref
            exact = projected._core_notice_cover_document_ref
            saved = read_notice_cover(db, exact)
            classification = read_consolidated_notice_cover(db, canonical).sources[0].public_bibliography.classification
            assert (classification.wire() if classification else None) == (
                {"genres": ["Astronomie"]} if expected else None)
            assert evict_notice_cover(db, exact, expected_epoch=saved.epoch)
        with closing(Storage(path)) as db:
            known = search_public_notice_c(db, "astronomie", field="subject")
            assert len(known.hits) == int(expected)
            if expected:
                assert known.hits[0].document == canonical
            assert not search_public_notice_c(db, "jeunesse", field="subject").hits
            assert db.entitlements() == db.acquisitions() == db.placements() == []
        assert calls == ["/index.php/feed"]
    finally:
        session.close()
