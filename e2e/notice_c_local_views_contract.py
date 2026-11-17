"""Book-detail-only GTK acceptance of Core-projected local public C.

Kept outside the shared probe entrypoint: plugin-manager-scroll does not
run this scenario or import these dependencies. No selector rule is relaxed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from time import monotonic

from gi.repository import Gdk


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def verify_local_notice_c_views(window, later, capture, report, complete):
    """Actual Library/Recent GTK routes with a Core-projected local C link.

    Fixture supplier envelopes and completed deliveries are synthetic; the
    OriginLink writer, C reconciliation, SQLite read, list and detail
    controllers and GTK paint are production paths. No action is clicked.
    """
    from concurrent.futures import ThreadPoolExecutor
    from io import BytesIO
    from tempfile import TemporaryDirectory
    from threading import get_ident
    from types import SimpleNamespace as NS

    import httpx
    from PIL import Image
    from cifereca.core.canonical_publication import publish_completed_acquisition
    from cifereca.core.source_convergence import SourceKey
    from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
    from cifereca.core.notice_c_local_origin_projection import project_local_origin_public_c
    from cifereca.core.notice_cover_repository import _opaque_key, is_notice_cover_document
    from cifereca.core.search_orchestration import _mirror_remote_results, _public_rows_from_response
    from cifereca.gui.book_widgets import BookListCell, CoverView
    from cifereca.gui.cover_loader import CoverLoader
    from cifereca.gui.document_cover_service import DocumentCoverService
    from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
    from cifereca.gui.query_model import QueryState
    from cifereca.models import SearchResult
    from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
    from cifereca.plugin_api.provider_rights_observation import (
        ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
    )
    from cifereca.storage import Storage

    tmp = TemporaryDirectory(prefix="cifereca-local-c-n4-")
    root = Path(tmp.name)
    filename = root / "local-c.sqlite"
    owner = window.service.current_identity.identity_id
    gtk_thread = get_ident()
    pool = ThreadPoolExecutor(max_workers=1)
    original_covers, original_n2 = window.covers, window.document_covers
    original_library = list(window._library_entries_cache)
    original_recent = list(window._recent_entries_cache)
    original_query = window.library_controls.state()
    state = {}
    gets = []
    url = "https://covers.example.org/local-origin-c.png"
    title = "Notice C locale témoin"
    pixels = (47, 108, 162)
    png = BytesIO()
    Image.new("RGB", (400, 600), pixels).save(png, format="PNG")
    body = png.getvalue()

    def seed():
        assert get_ident() != gtk_thread
        db = Storage(filename)
        try:
            db.identity_put({"identity_id": owner, "directory_name": owner,
                             "label": "Profil de recette"})
            book = root / "EBooks" / owner / "notice-c.pdf"
            book.parent.mkdir(parents=True)
            data = b"%PDF-1.7\nSynthetic completed local C publication\n"
            book.write_bytes(data)
            stamp = "2026-10-28T08:00:00+00:00"
            docs = []
            for provider, record in (("fixture-c-a", "edition-a"),
                                     ("fixture-c-b", "edition-b")):
                credential, account = "fixture-card", "fixture-buyer"
                connection = db.provider_connection_put({
                    "identity_id": owner, "provider_id": provider,
                    "credential_id": credential, "label": "Compte témoin",
                })
                db.provider_account_put({
                    "identity_id": owner, "provider_id": provider,
                    "credential_id": credential, "connection_id": connection,
                    "account_id": account, "label": "Compte témoin",
                })
                scope = ProviderRightsScope(owner, provider, credential, account)
                key = SourceKey(scope, record, "purchase", "purchase", "purchases")
                right = ProviderRightObservation(
                    record, "purchase", "purchase", "active", "provider_response",
                    "positive-right-observation", stamp, "unknown",
                )
                proof = ProviderRightsSnapshot(
                    scope, (right,), status="partial", coverage="partial",
                    branch="purchases",
                )
                result = publish_completed_acquisition(
                    db, library_root=root / "EBooks", path=book, source=key,
                    snapshot=proof, final_sha256=hashlib.sha256(data).hexdigest(),
                    delivery_receipt_ref="synthetic-completed-delivery",
                )
                row = SearchResult(
                    id=record, record_id=record, docbase="digital-books",
                    medium="digital", title=title, author="Autrice témoin",
                    type="book", format="PDF", publisher=None, published_at=None,
                    description=None, thumbnail_url=url, friendly_url=None,
                    scenario="search", source_provider=provider,
                    source_credential="__public__", source_collection="public_catalog",
                    source_account_id=None, owned=False, downloadable=False,
                    download_url=None, metadata={},
                )
                response = ProviderSearchResponse(
                    results=[row], public_notices=(PublicSearchNotice(row),),
                )
                rows = _public_rows_from_response(response, NS(
                    provider_id=provider, credential_id="__public__",
                    credentials={}, _accounts=[],
                ))
                assert len(rows) == 1
                assert _mirror_remote_results(
                    NS(storage=db), rows, identity_id=owner,
                    require_public_cover_witness=True,
                ) == 1
                docs.append(rows[0]._core_notice_cover_document_ref)
            doc = rows[0]._core_notice_canonical_cover_document_ref
            c = read_consolidated_notice_cover(db, doc)
            assert len(c.sources) == 2 and {x.url for x in c.candidates} == {url}
            with db._lock, db._connect() as sql:
                for source in docs:
                    sql.execute("DELETE FROM metadata WHERE key=?", (_opaque_key(source),))
            assert db.entitlements() == [] and db.acquisitions() == []
            return doc, c.epoch, result.artifact_id, result.placement_id, book
        finally:
            db.close()

    def cleanup_stage():
        if state.pop("attached", False):
            # Revoke the fixture bindings before restoring the normal loader.
            for page in (window.library_page, window.recent_page):
                if page._narrow_master_detail and page._narrow_detail_visible:
                    page.detail_back.emit("clicked")
                page.clear_rows()
            window.library_page.detail.empty("Sélectionnez un livre", "")
            window.recent_page.detail.empty("Sélectionnez un livre", "")
            window._library_objects = {}
            window._recent_objects = {}
            window._library_entries_cache = original_library
            window._recent_entries_cache = original_recent
            window.library_controls.set_state(original_query, notify=False)
            window.covers, window.document_covers = original_covers, original_n2
        for name in ("n2", "loader", "db"):
            value = state.pop(name, None)
            if value is not None:
                value.close()

    def cleanup():
        cleanup_stage()
        pool.shutdown(wait=True, cancel_futures=True)
        tmp.cleanup()

    def guard(fn):
        def run():
            try:
                fn()
            except BaseException:
                cleanup()
                raise
        return run

    job = pool.submit(seed)

    def await_job(future, done, deadline):
        if not future.done():
            assert monotonic() < deadline, "local C worker timeout"
            later(40, guard(lambda: await_job(future, done, deadline)))
            return
        done(future.result())

    def start_stage(offline):
        doc, epoch, artifact, placement, book = state["facts"]
        db = state["db"] = Storage(filename)
        loader = state["loader"] = CoverLoader(max_workers=2)
        loader.root = root / "images"
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

        def reader(ref):
            assert get_ident() != gtk_thread
            reads.append(ref)
            return cached_notice_cover_snapshot(NS(storage=db), ref)

        n2 = state["n2"] = DocumentCoverService(
            loader, cached_source_reader=reader,
            cached_source_scope=is_notice_cover_document,
        )

        def project():
            assert get_ident() != gtk_thread
            local = {
                "key": "artifact:" + artifact, "identity_id": owner,
                "artifact_id": artifact, "placement_id": placement,
                "local": True, "state": "local", "source": "catalog",
                "provider_id": "local", "providers": ["local"],
                "title": title, "author": "Autrice témoin", "media_format": "pdf",
                "medium": "digital", "path": str(book),
                "thumbnail_url": "https://covers.example.org/unqualified.png",
                "metadata": {},
            }
            recent = {**local, "key": placement, "acquired_at": stamp_value,
                      "updated_at": stamp_value}
            project_local_origin_public_c(db, [local, recent], identity_id=owner)
            for row in (local, recent):
                link = row["_public_canonical_cover_link"]
                assert link.document == doc and link.epoch == epoch
                assert len(link.source_refs) == 2
            return local, recent

        def show(rows):
            state["attached"] = True
            window.covers, window.document_covers = loader, n2
            window.library_controls.set_state(QueryState(), notify=False)
            window.library_page.clear_rows()
            window.recent_page.clear_rows()
            window._library_entries_cache = [rows[0]]
            window._recent_entries_cache = [rows[1]]
            show_route("library")

        label = "offline" if offline else "cold"

        def show_route(route):
            window.navigate(route)
            page = getattr(window, route + "_page")
            if page._narrow_master_detail and page._narrow_detail_visible:
                page.detail_back.emit("clicked")
            assert page.store.get_n_items() == 1
            row = page.store.get_item(0)
            assert row._cover_document_ref == doc
            assert row._cover_public_c_link.epoch == epoch
            assert row.thumbnail_url is None
            deadline = monotonic() + 8
            later(40, guard(lambda: wait_texture(route, page, row, "list", deadline)))

        def wait_texture(route, page, row, variant, deadline):
            assert window.view_stack.get_visible_child_name() == route
            if variant == "list":
                covers = [cell.cover for cell in walk(page)
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
                assert monotonic() < deadline, "local C N4 timeout: " + route + "/" + variant
                later(40, guard(lambda: wait_texture(route, page, row, variant, deadline)))
                return
            cover = covers[0]
            assert cover._loader is loader
            assert cover._request.source_uri == url and cover._request.still_current
            texture = cover.picture.get_paintable()
            with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                assert image.convert("RGB").getpixel((0, 0)) == pixels
            assert reads == [doc] and gets == ["GET"]
            capture("00-notice-c-" + route + "-" + variant + "-" + label)
            if variant == "list":
                page.selection.set_selected(0)
                assert page.selection.get_selected_item() is row
                later(40, guard(lambda: wait_texture(
                    route, page, row, "detail", monotonic() + 8,
                )))
                return
            if page._narrow_master_detail and page._narrow_detail_visible:
                page.detail_back.emit("clicked")
            if route == "library":
                show_route("recent")
                return
            stats = loader.stats()
            assert stats["ui_applied"] >= 4 and stats["ui_failed"] == 0
            assert stats["download_ok"] == (0 if offline else 1)
            report["notice_c_local_views_" + label] = {
                "views": 4, "sqlite_reads": len(reads),
                "image_gets_total": len(gets), "gtk_applied": stats["ui_applied"],
            }
            cleanup_stage()
            if not offline:
                start_stage(True)
            else:
                report["checks"]["P0_6366_mainwindow_library_recent_restart_offline"] = True
                cleanup()
                complete()

        stamp_value = 1794900000
        future = pool.submit(project)
        await_job(future, show, monotonic() + 10)

    def seeded(facts):
        state["facts"] = facts
        start_stage(False)

    later(40, guard(lambda: await_job(job, seeded, monotonic() + 10)))
