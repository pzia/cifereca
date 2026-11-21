"""#6397: same-call COPS catalogue web reference, through durable Core menu.

Controlled anonymous HTTP feed, no live catalogue or browser request.
"""
from contextlib import closing
from types import SimpleNamespace

import httpx
import pytest

from cifereca_ebooksgratuits import EbooksSession
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_web_choices import read_public_web_choices, resolve_public_web_choice
from cifereca.core.notice_cover_repository import evict_notice_cover, read_notice_cover
from cifereca.core.search_orchestration import _mirror_remote_results, _public_rows_from_response
from cifereca.storage import Storage


@pytest.mark.parametrize("alternate", ["exact", "absent", "wrong_book"])
def test_explicit_cops_book_reference_survives_eviction_and_reopen(tmp_path, alternate):
    host = "https://opds2.ebooksgratuits.org"
    link = host + "/index.php/book/42"
    html = "" if alternate == "absent" else (
        '<link rel="alternate" type="text/html" href="/index.php/book/'
        + ("42" if alternate == "exact" else "43") + '"/>'
    )
    feed = ('<feed xmlns="http://www.w3.org/2005/Atom">'
            '<title>All books</title><entry><id>urn:uuid:42</id>'
            '<title>Roman témoin COPS</title><author><name>Autrice témoin</name></author>'
            + html +
            '<link rel="http://opds-spec.org/acquisition/open-access" '
            'type="application/epub+zip" href="/index.php/fetch/42.epub"/>'
            '</entry></feed>')
    calls = []

    def transport(req):
        assert req.method == "GET"
        assert str(req.url) == host + "/index.php/feed"
        assert "cookie" not in req.headers and "authorization" not in req.headers
        calls.append("feed")
        return httpx.Response(200, text=feed, request=req)

    session = EbooksSession(credential_id="__public__", label="Public", credentials={},
                           options={}, config={}, diagnostics=None, transport=None)
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
    filename = tmp_path / "cops.sqlite"
    try:
        session.connect()
        result = session.search_with_response_facets("", "catalog")
        assert calls == ["feed"]
        with closing(Storage(filename)) as db:
            if alternate == "wrong_book":
                assert result.partial and result.results == [] and not result.public_notices
                assert _public_rows_from_response(result, session) == []
                assert not search_public_notice_c(db, "Roman témoin").hits
                assert db.entitlements() == db.acquisitions() == db.placements() == []
                return
            raw, = result.results
            notice, = result.public_notices
            assert notice.result is raw and raw.source_collection == "public_catalog"
            assert raw.owned is raw.downloadable is False and raw.download_url is None
            assert raw.friendly_url == (link if alternate == "exact" else None)
            row, = _public_rows_from_response(result, session)
            assert _mirror_remote_results(SimpleNamespace(storage=db), [row],
                                          identity_id="alice", require_public_cover_witness=True) == 1
            source_document = row._core_notice_cover_document_ref
            canonical = row._core_notice_canonical_cover_document_ref
            snapshot = read_consolidated_notice_cover(db, canonical)
            menu = read_public_web_choices(db, canonical)
            assert menu.epoch == snapshot.epoch
            if alternate == "exact":
                ref, = snapshot.catalogue_web_references
                choice, = menu.choices
                assert ref.url == link and ref.access_mode == "public"
                assert ref.evidence == "ebooksgratuits.public_search_record_identifier"
                assert ref.record_id == choice.record_id == raw.record_id
                assert ref.provider_id == choice.provider_id == session.provider_id
                assert resolve_public_web_choice(db, canonical, expected_epoch=menu.epoch,
                                                 choice_id=choice.choice_id) == link
            else:
                assert snapshot.catalogue_web_references == menu.choices == ()
            saved = read_notice_cover(db, source_document)
            assert evict_notice_cover(db, source_document, expected_epoch=saved.epoch)
            assert db.entitlements() == db.acquisitions() == db.placements() == []
        with closing(Storage(filename)) as db:
            assert read_notice_cover(db, source_document) is None
            assert read_public_web_choices(db, canonical) == menu
            known, = merge_public_notice_c_known_rows(
                [], search_public_notice_c(db, "Roman témoin", group_by_document=True),
                identity_id="bob",
            )
            assert known["_cover_document_ref"] == canonical
            if alternate == "exact":
                assert resolve_public_web_choice(db, canonical, expected_epoch=menu.epoch,
                                                 choice_id=choice.choice_id) == link
                assert resolve_public_web_choice(db, canonical, expected_epoch=menu.epoch + 1,
                                                 choice_id=choice.choice_id) is None
            assert db.entitlements() == db.acquisitions() == db.placements() == []
        assert calls == ["feed"]
    finally:
        session.close()
