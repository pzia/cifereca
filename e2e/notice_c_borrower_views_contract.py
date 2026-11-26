"""#6366: physical borrower C fallback through the real GTK views.

The BnF HTML parser and loan projector have already run in the parent
scenario. A controlled PUBLIC response envelope declares the exact parsed
catalogue fixture; BnF's legacy runtime API is NOT asserted to emit it.
The History occurrence is synthetic. C never grants a borrower right.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import get_ident
from time import monotonic
from types import SimpleNamespace as NS

import httpx
from gi.repository import Gdk
from PIL import Image

from cifereca.core.notice_c_borrower_public_projection import (
    attach_borrower_public_c, borrower_public_c_receipt,
)
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_cover_repository import _opaque_key, is_notice_cover_document
from cifereca.core.search_orchestration import _mirror_remote_results, _public_rows_from_response
from cifereca.gui.book_widgets import BookListCell, CoverView
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.models import HistoryItem, SearchResult
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.storage import Storage


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def verify_borrower_c_views(window, loan, record, descriptor, *,
                            later, capture, report, complete, checkpoint,
                            settled_capture=False):
    gtk_thread = get_ident()
    owner = window.service.current_identity.identity_id
    account_epoch = window.service._account_context_epoch
    source_epoch = window.service._source_context_epoch
    assert loan.identity_id == owner and type(loan.source_account_id) is str
    assert loan.source_provider == descriptor.id
    assert loan.record_id == record.resource_id and loan.title == record.title
    assert loan.medium == "digital"
    url = record.thumbnail_url
    from cifereca.core.search_orchestration import _shared_catalogue_cover_url
    assert type(url) is str and _shared_catalogue_cover_url(url) == url
    assert _shared_catalogue_cover_url(url + "?token=synthetic") is None
    pixels = (92, 37, 186)
    png = BytesIO()
    Image.new("RGB", (400, 600), pixels).save(png, format="PNG")
    body = png.getvalue()
    temp = TemporaryDirectory(prefix="cifereca-borrower-c-n4-")
    directory = Path(temp.name)
    filename = directory / "borrower.sqlite"
    pool = ThreadPoolExecutor(max_workers=1)
    original_loader, original_n2 = window.covers, window.document_covers
    state = {}
    gets = []

    def seed():
        assert get_ident() != gtk_thread
        db = Storage(filename)
        try:
            row = SearchResult(
                id="fixture:" + record.resource_id, record_id=record.resource_id,
                docbase="BNF_PRET", medium="digital", title=record.title,
                author=", ".join(record.authors) or None, type="book", format=None,
                publisher=record.publisher, published_at=None,
                description=record.description, thumbnail_url=url, friendly_url=None,
                scenario="BNF_PRET", source_provider=descriptor.id,
                source_credential="__public__", source_collection="public_catalog",
                source_account_id=None, owned=False, downloadable=False,
                download_url=None, metadata={},
            )
            response = ProviderSearchResponse(
                results=[row], public_notices=(PublicSearchNotice(row),),
            )
            admitted = _public_rows_from_response(response, NS(
                provider_id=descriptor.id, credential_id="__public__",
                credentials={}, _accounts=[],
            ))
            assert len(admitted) == 1 and admitted[0].thumbnail_url == url
            assert _mirror_remote_results(
                NS(storage=db), admitted, identity_id=owner,
                require_public_cover_witness=True,
            ) == 1
            admitted_row = admitted[0]
            doc = admitted_row._core_notice_canonical_cover_document_ref
            c = read_consolidated_notice_cover(db, doc)
            assert c is not None and len(c.sources) == 1
            assert [x.url for x in c.candidates] == [url]
            with db._lock, db._connect() as sql:
                sql.execute("DELETE FROM metadata WHERE key=?", (
                    _opaque_key(admitted_row._core_notice_cover_document_ref),
                ))
            assert db.entitlements() == [] and db.acquisitions() == []
            return doc, c.epoch
        finally:
            db.close()

    def close_stage():
        if state.pop("attached", False):
            window.loan_page.clear_rows()
            window.history_page.clear_rows()
            window.loan_objects = {}
            window.history_objects = {}
            window.service.last_loans = []
            window.service.last_history = []
            window.covers, window.document_covers = original_loader, original_n2
        for key in ("n2", "loader", "db"):
            value = state.pop(key, None)
            if value is not None:
                value.close()

    def cleanup():
        close_stage()
        pool.shutdown(wait=True, cancel_futures=True)
        temp.cleanup()

    def guard(fn):
        def run():
            try:
                fn()
            except BaseException:
                cleanup()
                raise
        return run

    def await_job(job, done, deadline):
        if not job.done():
            assert monotonic() < deadline, "borrower C worker timeout"
            later(40, guard(lambda: await_job(job, done, deadline)))
            return
        done(job.result())

    def start_stage(offline):
        doc, epoch = state["facts"]
        label = "offline" if offline else "cold"
        checkpoint("borrower_c_" + label + "_project")
        db = state["db"] = Storage(filename)
        loader = state["loader"] = CoverLoader(max_workers=2)
        loader.root = directory / "images"
        loader.root.mkdir(exist_ok=True)
        loader.preview_root = loader.root / "previews-v1"
        loader.preview_root.mkdir(exist_ok=True)
        reads = []

        def serve(req):
            gets.append(str(req.method))
            assert not offline and req.method == "GET" and str(req.url) == url
            return httpx.Response(200, content=body,
                                  headers={"content-type": "image/png"}, request=req)

        loader.client.close()
        loader.client = httpx.Client(transport=httpx.MockTransport(serve), trust_env=False)

        def read(ref):
            assert get_ident() != gtk_thread
            reads.append(ref)
            return cached_notice_cover_snapshot(NS(storage=db), ref)

        n2 = state["n2"] = DocumentCoverService(
            loader, cached_source_reader=read,
            cached_source_scope=is_notice_cover_document,
        )

        def project():
            assert get_ident() != gtk_thread
            # Borrower source is the actual normalized fixture occurrence;
            # remove its original direct image to exercise public C fallback.
            # A distinct historical occurrence must not reuse the loan right.
            borrowed = replace(loan, thumbnail_url=None, ebook_url=None, raw={})
            returned = HistoryItem(
                id="fixture-history:" + loan.record_id, provider=loan.provider,
                medium=loan.medium, record_id=loan.record_id, title=record.title,
                author=borrowed.author, thumbnail_url=None, default_thumbnail_url=None,
                borrowed_at="2026-10-01", returned_at="2026-10-15",
                source_provider=loan.source_provider,
                source_credential=loan.source_credential,
                source_account_id=loan.source_account_id, identity_id=owner,
            )
            assert attach_borrower_public_c(
                db, [borrowed, returned], identity_id=owner,
                account_epoch=account_epoch, source_epoch=source_epoch,
                descriptors=(descriptor,),
            ) == 2
            for item in (borrowed, returned):
                receipt = borrower_public_c_receipt(
                    item, identity_id=owner, account_epoch=account_epoch,
                    source_epoch=source_epoch,
                )
                assert receipt is not None and receipt.document == doc
                assert receipt.epoch == epoch and receipt.source_item is item
            foreign = replace(returned, identity_id="fixture-foreign")
            assert attach_borrower_public_c(
                db, [foreign], identity_id=owner, account_epoch=account_epoch,
                source_epoch=source_epoch, descriptors=(descriptor,),
            ) == 0
            assert db.entitlements() == [] and db.acquisitions() == []
            return borrowed, returned

        def show(rows):
            checkpoint("borrower_c_" + label + "_publish")
            state["attached"] = True
            window.loan_page.clear_rows()
            window.history_page.clear_rows()
            window.covers, window.document_covers = loader, n2
            # The preceding scenario proved EPUB N1 priority; explicitly
            # present a fresh image-less loan inventory with no local link.
            window._loan_cover_projection = None
            window._library_entries_cache = []
            window.loan_filter = "all"
            window._populate_loans([rows[0]])
            window.service.last_history = [rows[1]]
            window._populate_history([rows[1]])
            show_route("loans", window.loan_page)

        def show_route(route, page):
            checkpoint("borrower_c_" + label + "_" + route + "_list")
            window.navigate(route)
            later(100, guard(lambda: show_list(route, page)))

        def show_list(route, page):
            if page._narrow_master_detail and page._narrow_detail_visible:
                page.detail_back.emit("clicked")
            assert page.store.get_n_items() == 1
            row = page.store.get_item(0)
            assert row._cover_document_ref == doc
            assert row._cover_borrower_public_c_receipt.epoch == epoch
            assert row.thumbnail_url is None
            page.selection.set_selected(0xFFFFFFFF)
            later(100, guard(lambda: wait_texture(
                route, page, row, "list", monotonic() + 8,
            )))

        def wait_texture(route, page, row, variant, deadline, settled=False):
            assert window.view_stack.get_visible_child_name() == route
            if variant == "list":
                covers = [cell.cover for cell in walk(page.listview)
                          if isinstance(cell, BookListCell) and cell.get_mapped()
                          and cell._bound_key == row.key]
            else:
                covers = [cover for cover in walk(page.detail)
                          if isinstance(cover, CoverView) and cover.get_mapped()]
            ready = len(covers) == 1 and (
                isinstance(covers[0].picture.get_paintable(), Gdk.Texture)
                and not covers[0].placeholder.get_visible()
                and not covers[0].spinner.get_visible()
            )
            if not ready:
                assert monotonic() < deadline, "borrower C texture timeout"
                later(40, guard(lambda: wait_texture(route, page, row, variant, deadline)))
                return
            cover = covers[0]
            if settled_capture:
                from gi.repository import Gtk
                from notice_c_native_readability import stack_transition_running
                running = stack_transition_running(cover, window, Gtk.Stack)
                if running or not settled:
                    assert monotonic() < deadline, "borrower title transition timeout"
                    later(40, guard(lambda: wait_texture(
                        route, page, row, variant, deadline, settled=not running)))
                    return
            assert cover._loader is loader
            assert cover._request.source_uri == url and cover._request.still_current
            texture = cover.picture.get_paintable()
            with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                assert image.convert("RGB").getpixel((0, 0)) == pixels
            assert reads == [doc] and gets == ["GET"]
            assert window.service._account_context_epoch == account_epoch
            assert window.service._source_context_epoch == source_epoch
            stage = "12-c-" + route + "-" + variant + "-" + label
            if settled_capture:
                capture(stage, title_witness=(route, variant, record.title))
            else:
                capture(stage)
            if variant == "list":
                checkpoint("borrower_c_" + label + "_" + route + "_detail")
                page.selection.set_selected(0)
                assert page.selection.get_selected_item() is row
                later(100, guard(lambda: wait_texture(
                    route, page, row, "detail", monotonic() + 8,
                )))
                return
            if route == "loans":
                show_route("history", window.history_page)
                return
            stats = loader.stats()
            assert stats["ui_applied"] >= 4 and stats["ui_failed"] == 0
            assert stats["download_ok"] == (0 if offline else 1)
            report["notice_c_borrower_views_" + label] = {
                "views": 4, "sqlite_reads": len(reads),
                "image_gets_total": len(gets), "gtk_applied": stats["ui_applied"],
            }
            close_stage()
            if not offline:
                start_stage(True)
            else:
                report["checks"]["P0_6366_mainwindow_borrower_views_restart_offline"] = True
                checkpoint("borrower_c_complete")
                cleanup()
                complete()

        job = pool.submit(project)
        await_job(job, show, monotonic() + 10)

    def seeded(facts):
        state["facts"] = facts
        start_stage(False)

    checkpoint("borrower_c_seed")
    job = pool.submit(seed)
    later(40, guard(lambda: await_job(job, seeded, monotonic() + 10)))
