"""#6366/#4980: real GTK list/detail CoverView from durable public C.

Recorded Numilog HTML, exact Core admission, production N2 and N3, real GDK
texture. Image transport is synthetic; no provider network or UX sign-off.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import get_ident
from time import monotonic
from types import SimpleNamespace as NS
from unittest.mock import patch
import sys

import httpx
from gi.repository import Gdk, Gtk
from PIL import Image

from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover, read_notice_cover, is_notice_cover_document
from cifereca.core.search_orchestration import _mirror_remote_results, _public_rows_from_response
from cifereca.gui.book_widgets import BookListCell, CoverView
from cifereca.gui.query_model import QueryState
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "src/plugins/provider-numilog"
URL_SEARCH = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=loup"
)


def verify_notice_c_cover(window, later, capture, report, complete):
    """Asynchronous test fixture setup, then two mapped real GTK covers."""
    report["notice_c_cover_stage"] = "setup"
    temp = TemporaryDirectory(prefix="cifereca-c-n4-")
    directory = Path(temp.name)
    filename = directory / "notices.sqlite"
    pool = ThreadPoolExecutor(max_workers=1)
    state = {"db": None, "loader": None, "service": None, "probe": None}
    image_gets = []
    gtk_thread = get_ident()
    original_loader = window.covers
    original_service = window.document_covers
    original_query = window.search_controls.state()
    pixels = (84, 118, 149)
    payload = BytesIO()
    Image.new("RGB", (400, 600), pixels).save(payload, format="PNG")
    image_bytes = payload.getvalue()

    def seed():
        assert get_ident() != gtk_thread
        # The E2E checkout contains the actual provider and recorded page.
        import cifereca_numilog_public_provider as provider
        from cifereca_numilog_public import parse_public_search
        parsed = parse_public_search(
            (PLUGIN / "tests/fixtures/observed_search-loup-0.html").read_text(
                encoding="utf-8",
            ), URL_SEARCH,
        )
        calls = []
        session = provider.NumilogPublicSession(
            credential_id="__public__", label="Recorded public fixture",
            credentials={}, options={},
        )
        session.connect()
        db = Storage(filename)
        try:
            with patch.object(
                provider, "fetch_public_search",
                side_effect=lambda *_a, **_k: calls.append(1) or parsed,
            ):
                response = session.search_with_response_facets("loup", scope="catalog")
            rows = _public_rows_from_response(response, session)
            assert len(rows) == 1 and calls == [1]
            assert _mirror_remote_results(
                NS(storage=db), rows, identity_id="fixture",
                require_public_cover_witness=True,
            ) == 1
            row = rows[0]
            doc = row._core_notice_canonical_cover_document_ref
            fact = read_consolidated_notice_cover(db, doc)
            assert fact is not None and fact.epoch == 1
            assert len(fact.sources) == 1 and fact.candidates[0].url == row.thumbnail_url
            # Force the subsequent worker to rely on durable canonical C,
            # not the previously retained short-lived source observation.
            # Web action eligibility specifically requires qualified
            # eviction, never a raw DELETE with an ambiguous live fence.
            source_doc = row._core_notice_cover_document_ref
            source = read_notice_cover(db, source_doc)
            assert source is not None
            assert evict_notice_cover(db, source_doc, expected_epoch=source.epoch)
            assert read_notice_cover(db, source_doc) is None
            page = search_public_notice_c(db, "loup")
            assert len(page.hits) == 1 and page.hits[0].document == doc
            assert fact.catalogue_web_references[0].url == row.friendly_url
            from notice_c_web_menu_contract import seed_multiple_web_choices
            multi = seed_multiple_web_choices(db)
            return doc, fact.epoch, row.thumbnail_url, row.friendly_url, multi
        finally:
            db.close()
            session.close()

    def run_seed():
        # Provider performs lazy imports during construction/search/close.
        # Keep its source directory until all fixture activity has ended.
        sys.path.insert(0, str(PLUGIN))
        try:
            return seed()
        finally:
            sys.path.remove(str(PLUGIN))

    report["notice_c_cover_stage"] = "seed"
    seed_job = pool.submit(run_seed)
    deadline = monotonic() + 10

    def close_stage():
        if state.pop("mainwindow_stage", False):
            # Drop the real page's C rows/selection before closing its
            # temporary loader or restoring the desktop's original N2.
            page = window.search_page
            if page._narrow_master_detail and page._narrow_detail_visible:
                page.detail_back.emit("clicked")
            window._populate_local_search([])
            window.covers = original_loader
            window.document_covers = original_service
            window.search_controls.set_state(original_query, notify=False)
        for cover in state.get("covers", ()):
            cover.reset()
        for key in ("service", "loader", "db"):
            value = state.pop(key, None)
            if value is not None:
                value.close()
        probe = state.pop("probe", None)
        if probe is not None:
            probe.destroy()
        state["covers"] = ()

    def cleanup():
        close_stage()
        pool.shutdown(wait=True, cancel_futures=True)
        temp.cleanup()

    def guard(fn):
        def call():
            try:
                fn()
            except BaseException:
                cleanup()
                window.present()
                raise
        return call

    def after_seed():
        if not seed_job.done():
            assert monotonic() < deadline, "C seed worker timeout"
            later(40, guard(after_seed))
            return
        (state["document"], state["epoch"], state["url"], state["web_url"],
         state["multiple_web"]) = seed_job.result()
        start_stage(False)

    def start_stage(offline):
        report["notice_c_cover_stage"] = "offline_texture" if offline else "cold_texture"
        doc, epoch, url = state["document"], state["epoch"], state["url"]
        db = state["db"] = Storage(filename)
        loader = state["loader"] = CoverLoader(max_workers=2)
        # Own physical test cache, not the desktop application's existing N3.
        loader.root = directory / "images"
        loader.root.mkdir(exist_ok=True)
        loader.preview_root = loader.root / "previews-v1"
        loader.preview_root.mkdir(exist_ok=True)
        reads = []
        def serve(request):
            assert request.method == "GET" and str(request.url) == url
            image_gets.append("offline" if offline else "cold")
            if offline:
                raise AssertionError("network attempted after N3 restart")
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=image_bytes, request=request)
        loader.client.close()
        loader.client = httpx.Client(
            transport=httpx.MockTransport(serve), trust_env=False,
        )

        def reader(ref):
            assert get_ident() != gtk_thread
            reads.append(ref)
            return cached_notice_cover_snapshot(NS(storage=db), ref)

        service = state["service"] = DocumentCoverService(
            loader, cached_source_reader=reader,
            cached_source_scope=is_notice_cover_document,
        )
        probe = state["probe"] = Gtk.Window(
            transient_for=window, title="Notice C — témoin N4 liste/détail",
        )
        probe.set_default_size(330, 278)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        listing, detail = CoverView(width=60, height=90), CoverView(width=124, height=188)
        state["covers"] = (listing, detail)
        for cover in state["covers"]:
            box.append(cover)
        probe.set_child(box)
        probe.present()
        painted = {}
        for variant, cover in (("list", listing), ("detail", detail)):
            cover.bind_document(
                service, doc, variant=variant, minimum_revision=epoch,
                on_texture_applied=lambda uri, path, v=variant: painted.update({
                    v: (uri, path, get_ident()),
                }),
            )
        until = monotonic() + 8

        def verify():
            if set(painted) != {"list", "detail"}:
                assert monotonic() < until, "N4 texture timeout"
                later(40, guard(verify))
                return
            for variant, cover in (("list", listing), ("detail", detail)):
                texture = cover.picture.get_paintable()
                assert cover.get_mapped() and isinstance(texture, Gdk.Texture)
                assert texture.get_width() > 0 and texture.get_height() > 0
                assert not cover.placeholder.get_visible()
                assert not cover.spinner.get_visible()
                uri, path, thread = painted[variant]
                assert uri == url and thread == gtk_thread
                assert cover._request.source_uri == url and cover._request.still_current
                with Image.open(path) as image:
                    assert image.convert("RGB").getpixel((0, 0)) == pixels
                if variant == "detail":
                    assert path.read_bytes() == image_bytes
            assert reads == [doc]
            assert image_gets == ["cold"]
            stats = loader.stats()
            assert stats["ui_applied"] >= 2 and stats["ui_failed"] == 0
            assert stats["download_ok"] == (0 if offline else 1)
            label = "offline" if offline else "cold"
            report["notice_c_n4_" + label] = {
                "views": 2, "sqlite_reads": len(reads),
                "image_gets_total": len(image_gets),
                "gtk_applied": stats["ui_applied"],
                "downloads": stats["download_ok"],
            }
            capture("00-notice-c-n4-" + label)
            # Keep the exact same N2/N3 alive and demonstrate that the real
            # Search list factory/detail controller can paint this reference,
            # not just two manually bound CoverViews in a transient window.
            for cover in state["covers"]:
                cover.reset()
            state["covers"] = ()
            probe.destroy()
            state["probe"] = None
            window.present()
            verify_real_web_action(offline, db, lambda: verify_mainwindow_search(
                offline, db, service, loader, reads,
            ))

        later(40, guard(verify))



    def verify_real_web_action(offline, db, done):
        report["notice_c_cover_stage"] = "offline_web" if offline else "cold_web"
        from cifereca.gui.detail_panel import DetailPanel
        from cifereca.gui.notice_c_web_action_controller import request_public_web_actions
        doc, epoch = state["document"], state["epoch"]
        launches = []
        messages = []
        generation = [0]
        host = NS(service=NS(storage=db), runner=window.runner,
                  toast=messages.append, _closed=False)
        panel = DetailPanel(window)
        probe = state["probe"] = Gtk.Window(
            transient_for=window, title="Notice C — fiche web",
        )
        probe.set_default_size(340, 300)
        probe.set_child(panel)
        probe.present()

        def launch(uri, _context):
            assert get_ident() == gtk_thread
            launches.append(uri)

        assert request_public_web_actions(
            host, panel, doc, expected_epoch=epoch,
            current=lambda: generation[0] == 0,
            Gtk=Gtk, Gio=NS(AppInfo=NS(launch_default_for_uri=launch)),
        )
        until = monotonic() + 10

        def wait_menu():
            buttons = [button for button in panel._action_buttons
                       if getattr(button, "_cifereca_action_id", None) == "open-c-public-web"]
            if not buttons or not buttons[0].get_mapped():
                assert monotonic() < until, "GTK web action timeout"
                later(40, guard(wait_menu))
                return
            button, = buttons
            assert isinstance(button, Gtk.Button)
            assert not launches and not messages
            label = "offline" if offline else "cold"
            capture("00-notice-c-real-web-action-" + label)
            button.emit("clicked")

            def wait_launch():
                if not launches:
                    assert monotonic() < until, "GTK web action worker timeout"
                    later(40, guard(wait_launch))
                    return
                assert launches == [state["web_url"]] and not messages
                # Keep a physical reference to the old button after the
                # real DetailPanel revokes it. Even an emitted signal
                # cannot invoke the action again.
                panel.empty("Sélection modifiée", "Ancienne action révoquée")
                assert button._cifereca_action_context_revoked
                generation[0] += 1
                button.emit("clicked")
                assert launches == [state["web_url"]]
                assert image_gets == ["cold"]
                report["notice_c_web_action_" + label] = {
                    "gtk_mapped": True, "qualified_eviction": True,
                    "browser_sink_calls": len(launches), "old_button_revoked": True,
                }
                probe.destroy()
                state["probe"] = None
                window.present()
                report["notice_c_cover_stage"] = "offline_web_choices" if offline else "cold_web_choices"
                from notice_c_web_menu_contract import verify_multiple_web_choices
                verify_multiple_web_choices(
                    window, db, state["multiple_web"], offline=offline,
                    later=later, guard=guard, capture=capture, report=report,
                    complete=done, state=state,
                )

            later(40, guard(wait_launch))

        later(40, guard(wait_menu))


    def verify_mainwindow_search(offline, db, service, loader, reads):
        report["notice_c_cover_stage"] = "offline_search_list" if offline else "cold_search_list"
        doc, epoch, url = state["document"], state["epoch"], state["url"]
        identity = window.service.current_identity.identity_id
        assert identity
        # Read the durable C through the real cursor on a worker. The
        # normal MainWindow result publication consumes its public rows.
        def known_rows():
            assert get_ident() != gtk_thread
            return merge_public_notice_c_known_rows(
                [], search_public_notice_c(db, "loup"), identity_id=identity,
            )

        job = pool.submit(known_rows)
        until = monotonic() + 10

        def walk(widget):
            yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from walk(child)
                child = child.get_next_sibling()

        def show():
            if not job.done():
                assert monotonic() < until, "MainWindow C query timeout"
                later(40, guard(show))
                return
            rows = job.result()
            assert len(rows) == 1 and rows[0]["_cover_document_ref"] == doc
            assert rows[0]["metadata"]["notice_c_epoch"] == epoch
            assert rows[0]["thumbnail_url"] is None
            state["mainwindow_stage"] = True
            window.covers = loader
            window.document_covers = service
            window.search_controls.set_state(
                QueryState(text="loup", field="all", scope="all"), notify=False,
            )
            window.navigate("search")
            window._populate_local_search(rows)
            assert window.search_page.store.get_n_items() == 1
            row = window.search_page.store.get_item(0)
            assert row._cover_document_ref == doc and row._cover_public_c_epoch == epoch
            page = window.search_page
            # At narrow widths GTK deliberately shows only one pane. Test
            # the real list before selection, then the real detail after it.
            if page._narrow_master_detail and page._narrow_detail_visible:
                page.detail_back.emit("clicked")
            later(40, guard(lambda: check(row, "list", monotonic() + 8)))

        def check(row, variant, deadline):
            page = window.search_page
            if variant == "list":
                covers = [cell.cover for cell in walk(page) if isinstance(cell, BookListCell)
                          and cell.get_mapped() and cell._bound_key == row.key]
            else:
                covers = [cover for cover in walk(page.detail)
                          if isinstance(cover, CoverView) and cover.get_mapped()]
            if len(covers) != 1 or any(
                not cover.get_mapped()
                or not isinstance(cover.picture.get_paintable(), Gdk.Texture)
                or cover.placeholder.get_visible() or cover.spinner.get_visible()
                for cover in covers
            ):
                assert monotonic() < deadline, "MainWindow Search N4 timeout: " + variant
                later(40, guard(lambda: check(row, variant, deadline)))
                return
            assert window.view_stack.get_visible_child_name() == "search"
            cover = covers[0]
            assert cover._loader is loader
            assert cover._request.source_uri == url and cover._request.still_current
            texture = cover.picture.get_paintable()
            with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                assert image.convert("RGB").getpixel((0, 0)) == pixels
            label = "offline" if offline else "cold"
            capture("00-notice-c-mainwindow-search-" + variant + "-" + label)
            if variant == "list":
                report["notice_c_cover_stage"] = "offline_search_detail" if offline else "cold_search_detail"
                page.selection.set_selected(0)
                assert page.selection.get_selected_item() is row
                later(40, guard(lambda: check(row, "detail", monotonic() + 8)))
                return
            assert reads == [doc]
            assert image_gets == ["cold"]
            stats = loader.stats()
            assert stats["ui_applied"] >= 4 and stats["ui_failed"] == 0
            label = "offline" if offline else "cold"
            report["notice_c_mainwindow_search_" + label] = {
                "views": 2, "sqlite_reads": len(reads),
                "image_gets_total": len(image_gets),
                "gtk_applied": stats["ui_applied"],
            }
            close_stage()
            if not offline:
                start_stage(True)
                return
            report["checks"]["P0_6366_real_c_n2_n3_n4_restart_offline"] = True
            report["checks"]["P0_6366_mainwindow_search_list_detail_restart_offline"] = True
            report["checks"]["P0_6397_real_gtk_web_action_retained_c"] = True
            cleanup()
            window.present()
            report["notice_c_cover_stage"] = "complete"
            complete()

        later(40, guard(show))

    later(40, guard(after_seed))
