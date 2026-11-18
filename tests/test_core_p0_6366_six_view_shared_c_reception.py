"""#6366: one persisted edition, one N2, one physical N3 across six UI paths.

Core receives same-call supplier DTOs and persists actual OriginLinks (test
deliveries). The image transport is recorded locally, not a live provider.
Production row/detail methods execute headlessly; GTK pixel paint and actual
supplier parser/progressive orchestration remain separate E2E obligations.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
import struct
import zlib

import httpx

from cifereca.core.notice_c_borrower_public_projection import borrower_public_c_receipt
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_local_origin_projection import project_local_origin_public_c
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import _opaque_key, is_notice_cover_document
from cifereca.gui.borrower_public_c_bridge import (
    bind_borrower_public_c_detail, show_public_history_c, show_public_loan_c,
)
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.declared_document_cover import bind_declared_document_cover
from cifereca.gui.detail_model import book_detail_presentation
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.library_detail_controller import library_selected
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.gui.recent_detail_controller import recent_selected
from cifereca.models import Loan
from cifereca.storage import Storage

from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, publish, register, target,
)
from tests.test_core_p0_6366_borrower_public_c_lists_detail import (
    loan, history, host_for, cell, borrower_descriptors,
)
from tests.test_core_p0_6366_library_recent_public_c import (
    _views, library_view, recent_view,
)
from tests.test_core_p0_6366_origin_attested_public_c import _mirror
from tests.test_core_p0_6366_plugin_public_c_n3_revocation import _view, _book, _detail
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
from tests.test_core_p0_6366_same_call_public_cover_writer import witnessed
from tests.test_core_p0_6366_search_c_first_n3_scope import _cell_method, _search_selected
from tests.test_plugin_ui_host_boundary import host_type
from tests.test_search_results_view import _host

URL = "https://covers.example.org/reception-shared.png"
TITLE = "Livre source"
OWNER = "A"


def _png():
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">2I5B", 2, 3, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x18\x72\x91" * 2) * 3))
            + chunk(b"IEND", b""))


class Reception:
    """Same real N2/N3; only the final GTK surface is a recording sink."""

    def __init__(self, db, ref, epoch, transport, monkeypatch):
        self.ref, self.epoch = ref, epoch
        self.loader = CoverLoader(max_workers=2)
        monkeypatch.setattr(self.loader.client, "stream", transport)
        self.reads = []
        self.bindings = []
        self.handles = []

        def reader(document):
            self.reads.append(document)
            return cached_notice_cover_snapshot(NS(storage=db), document)

        self.service = DocumentCoverService(
            self.loader, cached_source_reader=reader,
            cached_source_scope=is_notice_cover_document,
        )

    def warm_index(self):
        # Cold N2 reads actual C asynchronously, not the legacy URL field.
        changed = Event()
        handle = self.service.get_cover(
            self.ref, "detail", lambda _: None,
            minimum_revision=self.epoch, on_source_changed=changed.set,
        )
        self.handles.append(handle)
        assert handle.source_uri is None
        assert changed.wait(10)
        assert not handle.still_current
        assert self.reads == [self.ref]

    def assert_n2_reads(self, bindings):
        # warm_index misses the source; the first binding then resolves N1
        # from that snapshot (a distinct N2 decision miss). Every subsequent
        # binding must hit that very same decision, without another SQL read.
        stats = self.service.stats()
        assert stats["n2_source_unknown"] == 1
        assert stats["n2_resolution_miss"] == 1
        assert stats["n2_miss"] == 2
        assert stats["n2_hit"] == bindings - 1
        assert stats["n2_hit"] + stats["n2_miss"] == bindings + 1

    def close(self):
        for request in self.handles:
            request.cancel()
        self.service.close()
        self.loader.close()

    def sink(self, view):
        def bind_document(service, document, *, variant, priority,
                          is_current=None, minimum_revision=None, **_):
            assert service is self.service and document == self.ref
            assert minimum_revision == self.epoch
            assert callable(is_current) and is_current() is True
            done, paths = Event(), []
            handle = service.get_cover(
                document, variant, lambda p: (paths.append(p), done.set()),
                minimum_revision=minimum_revision,
            )
            self.handles.append(handle)
            assert done.wait(10), (view, variant)
            assert handle.still_current and is_current()
            assert handle.source_uri == URL
            path = paths[-1]
            assert isinstance(path, Path) and path.is_file()
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            if variant == "detail":
                assert path.read_bytes() == _png()
            self.bindings.append((view, variant, document, path))
        return NS(
            bind_document=bind_document,
            bind_url=Mock(side_effect=AssertionError("legacy URL fallback")),
        )

    def listing(self, name, window, row):
        window.document_covers = self.service
        window.covers = self.loader
        sink = self.sink(name)
        widget = NS(
            window=window, cover=sink, cover_loader=self.loader,
            _thumbnail_item=row, _bound_key=row.key, _bind_epoch=1,
        )
        widget._admit_core_local_cover = (
            lambda *a: _cell_method("_admit_core_local_cover")(widget, *a)
        )
        widget._search_cover_guard = (
            lambda item: _cell_method("_search_cover_guard")(widget, item)
        )
        _cell_method("_thumbnail_changed")(widget, row)
        assert self.bindings[-1][:2] == (name, "list")

    def panel(self, name, panel=None):
        panel = panel or NS()
        def show(presentation, **_):
            assert presentation.cover_document_ref == self.ref
            assert presentation.thumbnail_url is None or name == "recommendations"
            assert bind_declared_document_cover(
                self.sink(name), self.loader, self.service,
                presentation.cover_document_ref, variant="detail", priority=0,
                is_current=panel._next_local_public_c_check,
                minimum_revision=panel._next_public_c_minimum_revision,
            )
        panel.show_presentation = show
        panel.empty = Mock(side_effect=AssertionError("detail unavailable"))
        return panel


def _search(db, reception):
    page = search_public_notice_c(db, TITLE)
    assert len({hit.document for hit in page.hits}) == 2  # homonym stays distinct
    known = merge_public_notice_c_known_rows([], page, identity_id=OWNER)
    source = next(x for x in known if x["_cover_document_ref"] == reception.ref)
    ui = _host()
    ui.service.current_identity.identity_id = OWNER
    ui._identity_context_generation = 2
    ui._search_generation = 3
    ui.service._account_context_epoch = 7
    ui.service._source_context_epoch = 9
    ui._populate_local_search(known)
    row = next(r for r in ui.search_page.show_rows.call_args.args[0]
               if r.key == source["key"])
    reception.listing("search", ui, row)
    panel = reception.panel("search")
    ui.search_page.detail = panel
    ui.search_page.selection.get_selected_item = lambda: row
    ui._closed = False
    ui._search_detail_request_generation = 0
    ui._cancel_cached_book_details = Mock()
    ui._selected_search_id = None
    _search_selected()(ui, row)
    assert reception.bindings[-1][:2] == ("search", "detail")


def _recommendations(source, reception):
    view, window, owner = _view()
    owner.current_identity.identity_id = OWNER
    ref, opaque = host_type()(window).public_notice_cover_binding(
        source, identity_id=OWNER,
    )
    assert ref == reception.ref
    view.show_rows((replace(_book(document=ref), cover_document_receipt=opaque),))
    row = view._page.rows[0]
    view._page.selection.current = row
    reception.listing("recommendations", window, row)
    reception.panel("recommendations", view._page.detail)
    view.show_detail(_detail(document=ref))
    assert reception.bindings[-1][:2] == ("recommendations", "detail")


def _local(db, reception, artifact, placement, path):
    library, recent = _views(db, artifact, placement, path)
    for item in (library, recent):
        item.update(title=TITLE, author="Autrice")
    project_local_origin_public_c(db, [library, recent], identity_id=OWNER)
    no_op = lambda *a, **k: None
    service = NS(
        current_identity=NS(identity_id=OWNER), _account_context_epoch=7,
        _source_context_epoch=9, provider_display_name=str,
        identity_by_id=lambda _: None,
    )
    for name, item, row in (
        ("library", library, library_view(library)),
        ("recent", recent, recent_view(recent)),
    ):
        assert item["_public_canonical_cover_link"].document == reception.ref
        panel = reception.panel(name)
        host = NS(
            _closed=False, service=service,
            _library_objects={row.key: item} if name == "library" else {},
            _recent_objects={row.key: item} if name == "recent" else {},
            library_page=NS(detail=panel), recent_page=NS(detail=panel),
            _cancel_cached_book_details=no_op, _book_description=lambda _: "",
            _library_availability_label=lambda _: "Local",
            _kobo_backup_status_label=lambda *_: "",
            _add_local_book_actions=no_op, _add_book_recommendation_action=no_op,
            _add_book_notice=no_op, _add_facets=no_op, _add_plugin_book_actions=no_op,
            _request_cached_book_details=no_op,
            library_controls=object(), search_controls=object(),
        )
        reception.listing(name, host, row)
        if name == "library":
            library_selected(
                host, row, Gtk=NS(), unique_library_loan=lambda *_: None,
                book_detail_presentation=book_detail_presentation,
                detail_language=lambda value: value, document_links=lambda _: (),
            )
        else:
            recent_selected(host, row, Path=Path,
                            format_recent_timestamp=lambda *a, **k: "Aujourd'hui")
        assert reception.bindings[-1][:2] == (name, "detail")


def _borrowers(db, reception):
    from cifereca.core.notice_c_borrower_public_projection import attach_borrower_public_c
    items = [loan(identity_id=OWNER), history(identity_id=OWNER)]
    assert attach_borrower_public_c(
        db, items, identity_id=OWNER, account_epoch=7, source_epoch=9,
        descriptors=borrower_descriptors(),
    ) == 2
    for item in items:
        name = "loans" if type(item) is Loan else "history"
        receipt = borrower_public_c_receipt(item, identity_id=OWNER)
        assert receipt.document == reception.ref
        host = host_for(item, identity=OWNER)
        row = cell(host, item, receipt)
        row._borrower_public_c_context = (OWNER, 7, 9)
        reception.listing(name, host, row)

        def show(presentation, *, document):
            assert presentation is item
            assert bind_borrower_public_c_detail(
                host, reception.sink(name), document, receipt,
            )

        if type(item) is Loan:
            host.loan_page.detail.show_loan_public_c = show
            assert show_public_loan_c(host, item, lambda: item)
        else:
            host.history_page.detail.show_history_public_c = show
            assert show_public_history_c(host, item, item)
        assert reception.bindings[-1][:2] == (name, "detail")

    # Same private DTO coordinates under another owner do not give a receipt.
    assert all(borrower_public_c_receipt(item, identity_id="B") is None
               for item in items)


def _audit_real_cover(db, reception, artifact, placement, path, *, physical):
    """Retained C -> genuine observer -> N2/N3 -> scoped broker, no image load."""
    from cifereca.brokers import CatalogBrokerImpl
    from cifereca.gui.local_cover_adapter import LocalCoverEvidenceAdapter

    row, _ = _views(db, artifact, placement, path)
    row.update(title=TITLE, author="Autrice")
    project_local_origin_public_c(db, [row], identity_id=OWNER)
    link = row["_public_canonical_cover_link"]
    assert link.document == reception.ref and link.epoch == reception.epoch

    def library_entries(*, identity_id, scan):
        assert identity_id == OWNER and scan is False
        return [row]

    host = NS(storage=db, identity_by_id=lambda owner: object() if owner == OWNER else None,
              _account_context_epoch=7, _source_context_epoch=9,
              library_entries=library_entries)
    broker = CatalogBrokerImpl(host)
    before = reception.service.stats()
    with_observer = LocalCoverEvidenceAdapter()
    try:
        with_observer.bind_audit_observer(host, reception.service)
        facts = broker.library_cover_audit(identity_id=OWNER)
        assert facts["status"] == "partial" and facts["entries"] == 1
        assert facts["catalog"] == facts["n2"]["catalog"] == 1
        assert facts["n3"][physical] == 1
        assert sum(facts["n3"].values()) == 1
        assert URL not in repr(facts) and str(path) not in repr(facts)
        assert broker.library_cover_audit(identity_id="B")["status"] == "unavailable"
    finally:
        with_observer.close()
    assert host._documentary_cover_observer is None
    detached = broker.library_cover_audit(identity_id=OWNER)
    assert detached["catalog"] == detached["n2"]["unknown"] == detached["n3"]["unknown"] == 1
    assert reception.service.stats() == before


def test_same_public_c_six_list_detail_paths_and_reopened_offline_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    filename = tmp_path / "reception.sqlite"
    db = Storage(filename)
    png = _png()
    gets = []

    @contextmanager
    def transport(method, url):
        assert method == "GET" and url == URL
        gets.append(url)
        yield httpx.Response(
            200, content=png, headers={"content-type": "image/png"},
            request=httpx.Request(method, url),
        )

    try:
        path = target(tmp_path, OWNER)
        # Real canonical publication writer, two completed origin proofs,
        # one owner and one artifact. No manually injected C relation.
        for provider, edition, credential in (
            ("bnf-pret", "book-42", "card-a"),
            ("seller-b", "edition-b", "card-b"),
        ):
            register(db, OWNER, provider, credential)
            key, rights = new_source(
                OWNER, provider=provider, edition=edition, credential=credential,
            )
            result = publish(db, tmp_path / "EBooks", path, key, rights)
        artifact, placement = result.artifact_id, result.placement_id

        def source(provider, record, docbase):
            return witnessed(public_row(
                source_provider=provider, record_id=record, docbase=docbase,
                title=TITLE, author="Autrice", thumbnail_url=URL,
            ))
        first = source("bnf-pret", "book-42", "BNF_PRET")
        other = source("seller-b", "edition-b", "digital-books")
        homonym = source("seller-b", "unrelated", "digital-books")
        assert _mirror(db, first) == 1
        assert _mirror(db, other) == 1
        live = source("bnf-pret", "book-42", "BNF_PRET")
        assert _mirror(db, live) == 1
        assert _mirror(db, homonym) == 1
        ref = live._core_notice_canonical_cover_document_ref
        snapshot = read_consolidated_notice_cover(db, ref)
        assert len(snapshot.sources) == 2
        assert ref == other._core_notice_canonical_cover_document_ref
        assert homonym._core_notice_canonical_cover_document_ref != ref
        reception = Reception(db, ref, snapshot.epoch, transport, monkeypatch)
        try:
            reception.warm_index()
            _search(db, reception)
            _recommendations(live, reception)
            _local(db, reception, artifact, placement, path)
            _borrowers(db, reception)
            assert len(reception.bindings) == 12
            assert {x[0] for x in reception.bindings} == {
                "search", "recommendations", "library", "recent", "loans", "history",
            }
            assert gets == [URL]
            assert reception.reads == [ref]
            reception.assert_n2_reads(12)
            assert reception.loader.stats()["download_ok"] == 1
            _audit_real_cover(db, reception, artifact, placement, path, physical="signature_valid")
            assert gets == [URL]

            # N2 remains warm while only the physical source disappears.
            # The detail variant deliberately bypasses the preview cache.
            image = reception.loader._path(URL)
            assert image.read_bytes() == png
            image.unlink()
            _audit_real_cover(db, reception, artifact, placement, path, physical="not_validated")
            assert not image.exists() and gets == [URL]
            done, paths = Event(), []
            handle = reception.service.get_cover(
                ref, "detail", lambda p: (paths.append(p), done.set()),
                minimum_revision=snapshot.epoch,
            )
            reception.handles.append(handle)
            assert done.wait(10)
            assert handle.still_current and handle.source_uri == URL
            assert paths == [image] and image.read_bytes() == png
            assert gets == [URL, URL]
            assert reception.reads == [ref]
            reception.assert_n2_reads(13)
            assert reception.loader.stats()["download_ok"] == 2
        finally:
            reception.close()
        assert db.entitlements() == [] and db.acquisitions() == []
        with db._lock, db._connect() as sql:
            for row in (live, other, homonym):
                sql.execute("DELETE FROM metadata WHERE key=?",
                            (_opaque_key(row._core_notice_cover_document_ref),))
    finally:
        db.close()

    # Reopen the database AND physical cache with a new service and loader.
    # No live witness survives restart. The real C cursor and the exact
    # cached source worker are two independent, read-only recommendation routes.
    restored = Storage(filename)

    @contextmanager
    def offline(*a, **k):
        gets.append("offline GET")
        raise AssertionError("network attempted after restart")
        yield

    reception = Reception(restored, ref, snapshot.epoch, offline, monkeypatch)
    try:
        reception.warm_index()
        _search(restored, reception)
        from cifereca.core.notice_c_recommendation_known import known_public_notice_candidates
        from cifereca.core.search_orchestration import _stamp_cached_public_notice_refs
        from cifereca.core.notice_c_recommendation_known import known_public_c_binding_facts
        known = next(row for row in known_public_notice_candidates(restored, TITLE)
                     if known_public_c_binding_facts(row)[0] == ref)
        _recommendations(known, reception)
        cached = replace(live)
        _stamp_cached_public_notice_refs([cached], identity_id=OWNER, storage=restored)
        assert cached._core_notice_canonical_cover_document_ref == ref
        _recommendations(cached, reception)
        _local(restored, reception, artifact, placement, path)
        _borrowers(restored, reception)
        assert len(reception.bindings) == 14
        reception.assert_n2_reads(14)
        _audit_real_cover(restored, reception, artifact, placement, path, physical="signature_valid")
        assert reception.reads == [ref]
        assert gets == [URL, URL]

        # The homonym has exactly the same physical URL but a different C.
        # Its first N2 read must consult its own canonical source; N3 can
        # reuse the image without either a third GET or a documentary merge.
        foreign_ref = homonym._core_notice_canonical_cover_document_ref
        foreign_snapshot = read_consolidated_notice_cover(restored, foreign_ref)
        assert foreign_ref != ref and foreign_snapshot is not None
        changed = Event()
        cold = reception.service.get_cover(
            foreign_ref, "detail", lambda _: None,
            minimum_revision=foreign_snapshot.epoch, on_source_changed=changed.set,
        )
        reception.handles.append(cold)
        assert cold.source_uri is None and changed.wait(10)
        assert not cold.still_current
        seen, ready = [], Event()
        warm = reception.service.get_cover(
            foreign_ref, "detail", lambda p: (seen.append(p), ready.set()),
            minimum_revision=foreign_snapshot.epoch,
        )
        reception.handles.append(warm)
        assert ready.wait(10) and warm.still_current
        assert warm.source_uri == URL and seen[0].read_bytes() == png
        assert reception.reads == [ref, foreign_ref]
        assert gets == [URL, URL]
        assert reception.loader.stats()["download_ok"] == 0
        assert restored.entitlements() == [] and restored.acquisitions() == []
    finally:
        reception.close()
        restored.close()
