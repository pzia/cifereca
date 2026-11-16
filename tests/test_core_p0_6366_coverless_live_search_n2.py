"""#6366: live image-less public Search uses the SAME C/N2 as C-first Search.

No image is not a missing-cover attestation. A canonical source is usable for
list/detail only after the exact same-call Core producer persisted it.
"""
from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS

from cifereca.core.notice_cover_repository import is_notice_cover_document
from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
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
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
from tests.test_core_p1_6168_search_remote_document_gateway import host


def test_coverless_live_search_list_and_detail_share_pending_c_without_n3(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = Storage(tmp_path / "coverless-live.sqlite")
    loader = CoverLoader(max_workers=2)
    downloads, reads, placeholders = [], [], []
    monkeypatch.setattr(
        loader, "load",
        lambda url, callback, **kwargs: downloads.append(url),
    )
    monkeypatch.setattr(
        loader, "load_preview",
        lambda url, callback, **kwargs: downloads.append(url),
    )

    def read(document):
        reads.append(document)
        return cached_notice_cover_snapshot(NS(storage=db), document)

    service = DocumentCoverService(
        loader, cached_source_reader=read,
        cached_source_scope=is_notice_cover_document,
    )
    try:
        live = witnessed(public_row(record_id="no-cover", thumbnail_url=None))
        window, _, key = host(live)
        assert mirror(db, [live]) == 1
        canonical = live._core_notice_canonical_cover_document_ref
        evidence = exact_remote_search_document(window, live, key)
        assert evidence is not None
        assert evidence.document == canonical and evidence.candidates == ()
        assert register_exact_search_document(
            window, live, service, evidence, canonical,
        )

        ready = Event()
        cold = service.get_cover(
            canonical, "list", placeholders.append,
            on_source_changed=ready.set,
        )
        assert ready.wait(10)
        assert not cold.still_current
        listed = service.get_cover(canonical, "list", placeholders.append)
        detailed = service.get_cover(canonical, "detail", placeholders.append)
        assert listed.source_uri is None and detailed.source_uri is None
        assert listed.still_current and detailed.still_current
        assert reads == [canonical] and downloads == []
        assert placeholders == [None, None, None]
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        service.close()
        loader.close()
        db.close()


def test_coverless_search_ref_refuses_unattested_cloned_and_cross_edition(
    tmp_path,
):
    db = Storage(tmp_path / "coverless-forgery.sqlite")
    try:
        raw = public_row(thumbnail_url=None)
        window, _, key = host(raw)
        assert exact_remote_search_document(window, raw, key) is None

        live = witnessed(raw)
        window.search_objects[key] = live
        assert exact_remote_search_document(window, live, key) is None
        assert mirror(db, [live]) == 1
        canonical = live._core_notice_canonical_cover_document_ref
        assert exact_remote_search_document(window, live, key).document == canonical

        clone = replace(live)
        window.search_objects[key] = clone
        assert exact_remote_search_document(window, clone, key) is None
        window.search_objects[key] = live
        live._core_notice_canonical_cover_document_ref = (
            canonical_cover_document_ref("edition:someone-else")
        )
        assert exact_remote_search_document(window, live, key) is None
        live._core_notice_canonical_cover_document_ref = canonical
        window._search_generation += 1
        assert exact_remote_search_document(window, live, key) is None
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
