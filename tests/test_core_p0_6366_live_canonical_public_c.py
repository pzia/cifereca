"""#6366: same-call public Search -> exact source -> canonical C -> N2.

This does NOT equate provider editions by title/ISBN or image similarity.
Only a source's own attested edition can be automatically published.
"""
from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS

from cifereca.core.notice_c_cover_index import (
    canonical_cover_document_ref, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import read_notice_cover, is_notice_cover_document
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.gui.search_document_cover_bridge import (
    exact_remote_search_document, register_exact_search_document,
)
from cifereca.storage import Storage
from tests.test_core_p0_6366_same_call_public_cover_writer import (
    witnessed, mirror,
)
from tests.test_core_p0_6366_real_search_notice_c_ingestion import (
    public_row, URL, OTHER_URL,
)
from tests.test_core_p1_6168_search_remote_document_gateway import (
    anonymous_catalogue, host,
)


def test_live_attested_source_is_written_and_read_as_canonical_c_by_known_search(tmp_path):
    db = Storage(tmp_path / "source.sqlite")
    try:
        live = witnessed(public_row())
        assert mirror(db, [live]) == 1
        exact = live._core_notice_cover_document_ref
        source = read_notice_cover(db, exact)
        canonical = canonical_cover_document_ref(source.observation.key.edition_id)
        assert canonical != exact
        assert live._core_notice_canonical_cover_document_ref == canonical
        assert live._core_notice_canonical_cover_document_epoch == 1
        index = read_consolidated_notice_cover(db, canonical)
        assert index is not None
        assert len(index.sources) == 1
        assert index.sources[0].source_ref == exact.source_key
        assert [candidate.url for candidate in index.candidates] == [URL]
        page = search_public_notice_c(db, "Même titre")
        assert len(page.hits) == 1 and page.hits[0].document == canonical
        for identity in ("alice", "bob"):
            rows = merge_public_notice_c_known_rows(
                [], page, identity_id=identity,
            )
            assert len(rows) == 1
            assert rows[0]["_cover_document_ref"] == canonical
            assert rows[0]["state"] == "remote_catalog"
            assert not rows[0].get("download_url")
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_same_edition_revisions_refresh_canonical_epoch_without_visitor_duplication(
    tmp_path, monkeypatch,
):
    from cifereca.core import search_orchestration
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    db = Storage(tmp_path / "revisions.sqlite")
    try:
        first = witnessed(public_row())
        assert mirror(db, [first]) == 1
        canonical = first._core_notice_canonical_cover_document_ref
        assert read_consolidated_notice_cover(db, canonical).epoch == 1

        clock[0] = 201
        second = witnessed(public_row(thumbnail_url=OTHER_URL))
        assert mirror(db, [second]) == 1
        assert second._core_notice_canonical_cover_document_ref == canonical
        assert second._core_notice_canonical_cover_document_epoch == 2
        index = read_consolidated_notice_cover(db, canonical)
        assert index.epoch == 2
        assert [x.url for x in index.candidates] == [OTHER_URL]
        assert len(index.sources) == 1

        clock[0] = 202
        third = witnessed(public_row(thumbnail_url=OTHER_URL))
        assert mirror(db, [third]) == 1
        assert third._core_notice_canonical_cover_document_ref == canonical
        assert third._core_notice_canonical_cover_document_epoch == 2
        assert read_consolidated_notice_cover(db, canonical).epoch == 2
        assert search_public_notice_c(db, "Même titre").hits[0].document == canonical
    finally:
        db.close()


def test_two_homonymous_sources_never_merge_to_one_canonical_edition(tmp_path):
    db = Storage(tmp_path / "separate.sqlite")
    try:
        a = witnessed(public_row(record_id="edition-A"))
        b = witnessed(public_row(record_id="edition-B"))
        assert mirror(db, [a, b]) == 2
        ca = a._core_notice_canonical_cover_document_ref
        cb = b._core_notice_canonical_cover_document_ref
        assert ca != cb
        assert [x.url for x in read_consolidated_notice_cover(db, ca).candidates] == [URL]
        assert [x.url for x in read_consolidated_notice_cover(db, cb).candidates] == [URL]
        found = search_public_notice_c(db, "Même titre")
        assert len(found.hits) == 2
        assert {x.document for x in found.hits} == {ca, cb}
    finally:
        db.close()


def test_same_call_missing_or_replayed_row_cannot_publish_canonical_c(tmp_path):
    db = Storage(tmp_path / "no-claims.sqlite")
    try:
        raw = public_row()
        assert mirror(db, [raw]) == 1
        assert not hasattr(raw, "_core_notice_canonical_cover_document_ref")
        live = witnessed(raw)
        clone = replace(live)
        assert mirror(db, [clone]) == 1
        assert not hasattr(clone, "_core_notice_canonical_cover_document_ref")
        assert mirror(db, [live]) == 1
        assert hasattr(live, "_core_notice_canonical_cover_document_ref")
        canonical = live._core_notice_canonical_cover_document_ref
        live.thumbnail_url = OTHER_URL
        assert mirror(db, [live]) == 1
        assert not hasattr(live, "_core_notice_canonical_cover_document_ref")
        assert read_consolidated_notice_cover(db, canonical).epoch == 1
    finally:
        db.close()


def test_live_list_detail_shared_canonical_n2_reader_and_foreign_ref_rejected(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = Storage(tmp_path / "views.sqlite")
    loader = CoverLoader(max_workers=2)
    calls, reads = [], []
    monkeypatch.setattr(
        loader, "load", lambda url, callback, **kw: (
            calls.append(url) or NS(cancel=lambda: None)
        ),
    )
    monkeypatch.setattr(
        loader, "load_preview", lambda url, callback, **kw: (
            calls.append(url) or NS(cancel=lambda: None)
        ),
    )
    def reader(document):
        reads.append(document)
        return cached_notice_cover_snapshot(NS(storage=db), document)
    covers = DocumentCoverService(
        loader, cached_source_reader=reader,
        cached_source_scope=is_notice_cover_document,
    )
    try:
        live = witnessed(anonymous_catalogue())
        window, _, key = host(live)
        assert mirror(db, [live]) == 1
        ref = live._core_notice_canonical_cover_document_ref
        proof = exact_remote_search_document(window, live, key)
        assert proof is not None and proof.document == ref
        assert register_exact_search_document(window, live, covers, proof, ref)
        event = Event()
        pending = covers.get_cover(
            ref, "list", lambda _: None, on_source_changed=event.set,
        )
        assert event.wait(10) and not pending.still_current
        listed = covers.get_cover(ref, "list", lambda _: None)
        detailed = covers.get_cover(ref, "detail", lambda _: None)
        assert listed.source_uri == detailed.source_uri == live.thumbnail_url
        assert reads == [ref]
        assert calls and all(url == live.thumbnail_url for url in calls)

        foreign = canonical_cover_document_ref("edition:foreign")
        live._core_notice_canonical_cover_document_ref = foreign
        fallback = exact_remote_search_document(window, live, key)
        assert fallback is not None
        assert fallback.document == live._core_notice_cover_document_ref
        assert fallback.document != foreign
    finally:
        covers.close()
        loader.close()
        db.close()
