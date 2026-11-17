"""#6366: an attested newer C receipt never reads an older N2 decision."""
from threading import Event
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_cover_repository import is_notice_cover_document
from cifereca.core.covers import candidate
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import (
    CoverSourceSnapshot, DocumentCoverService,
)


DOCUMENT = canonical_cover_document_ref("edition:n2-revision-floor")
OLD = "https://covers.example.org/old-c.jpg"
NEW = "https://covers.example.org/current-c.jpg"


def snapshot(revision, uri):
    return CoverSourceSnapshot(
        scope=DOCUMENT, revision=revision,
        candidates=(candidate(
            uri, tier="catalog", source="public",
            evidence_id="edition:revision-floor",
        ),),
    )


def test_current_c_receipt_blocks_hot_old_n2_for_both_variants(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader(max_workers=1)
    entered, release, changed = Event(), Event(), Event()
    reads, materialized, delivered = [], [], []

    def reader(document):
        reads.append(document)
        entered.set()
        assert release.wait(10)
        return snapshot(2, NEW)

    def load(url, callback, **kwargs):
        materialized.append(url)
        return NS(cancel=lambda: None)

    monkeypatch.setattr(loader, "load", load)
    monkeypatch.setattr(loader, "load_preview", load)
    service = DocumentCoverService(
        loader, cached_source_reader=reader,
        cached_source_scope=is_notice_cover_document,
    )
    try:
        service.register(snapshot(1, OLD))
        assert service.get_cover(DOCUMENT, "list", lambda _: None).source_uri == OLD
        materialized.clear()

        first = service.get_cover(
            DOCUMENT, "detail", delivered.append,
            on_source_changed=changed.set, minimum_revision=2,
        )
        assert entered.wait(10)
        second = service.get_cover(
            DOCUMENT, "list", delivered.append, minimum_revision=2,
        )
        assert first.source_uri is second.source_uri is None
        assert materialized == []
        assert delivered == [None, None]
        assert reads == [DOCUMENT]
        release.set()
        assert changed.wait(10)
        assert not first.still_current and not second.still_current
        for kind in ("list", "detail"):
            assert service.get_cover(
                DOCUMENT, kind, lambda _: None, minimum_revision=2,
            ).source_uri == NEW
        assert materialized == [NEW, NEW]
        assert reads == [DOCUMENT]
    finally:
        release.set()
        service.close()
        loader.close()


def test_unsatisfied_epoch_never_materializes_stale_snapshot(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader(max_workers=1)
    seen = []
    monkeypatch.setattr(loader, "load", lambda *a, **kw: seen.append(a))
    service = DocumentCoverService(loader)
    try:
        service.register(snapshot(1, OLD))
        results = []
        result = service.get_cover(
            DOCUMENT, "detail", results.append, minimum_revision=2,
        )
        assert result.source_uri is None and results == [None]
        assert seen == []
        for invalid in (True, 0, -1, 2**63, "2"):
            with pytest.raises(ValueError, match="revision"):
                service.get_cover(
                    DOCUMENT, "detail", results.append,
                    minimum_revision=invalid,
                )
    finally:
        service.close()
        loader.close()


def test_search_c_first_list_and_detail_use_persisted_receipt_epoch(tmp_path):
    from unittest.mock import Mock
    from tests.test_core_p0_6366_search_c_first_n3_scope import (
        _seed, _cell_method, _search_selected,
    )
    db, host, row, item = _seed(tmp_path)
    try:
        receipt = row._cover_known_public_c_receipt
        cell = NS(window=host, _bind_epoch=1, _bound_key=row.key,
                  _thumbnail_item=row)
        options = _cell_method("_search_cover_guard")(cell, row)
        assert options["minimum_revision"] == receipt.epoch
        assert options["is_current"]()

        observed = []
        panel = NS(empty=Mock(), _next_local_public_c_check=None)
        def render(*args, **kw):
            observed.append((panel._next_public_c_minimum_revision,
                             panel._next_local_public_c_check))
        panel.show_presentation = render
        controller = NS(
            _closed=False, service=host.service, _search_mode="local",
            _identity_context_generation=2, _search_generation=3,
            _search_detail_request_generation=0,
            _cancel_cached_book_details=lambda: None,
            _search_local_objects=host._search_local_objects,
            _selected_search_id=None,
            search_page=NS(detail=panel, selection=NS(
                get_selected_item=lambda: row,
            )),
        )
        _search_selected()(controller, row)
        assert observed[0][0] == receipt.epoch
        assert observed[0][1]()
        assert panel._next_public_c_minimum_revision is None

        # A fresh detail selection of the SAME row is not the old N3 lease.
        _search_selected()(controller, row)
        assert not observed[0][1]()
        assert observed[1][1]()
        controller._search_generation += 1
        _search_selected()(controller, row)
        assert len(observed) == 2
        panel.empty.assert_called_once()
        assert not observed[1][1]()
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
