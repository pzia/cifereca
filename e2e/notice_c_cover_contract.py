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
from cifereca.core.notice_cover_repository import _opaque_key, is_notice_cover_document
from cifereca.core.search_orchestration import _mirror_remote_results, _public_rows_from_response
from cifereca.gui.book_widgets import CoverView
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
    temp = TemporaryDirectory(prefix="cifereca-c-n4-")
    directory = Path(temp.name)
    filename = directory / "notices.sqlite"
    pool = ThreadPoolExecutor(max_workers=1)
    state = {"db": None, "loader": None, "service": None, "probe": None}
    image_gets = []
    gtk_thread = get_ident()
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
            with db._lock, db._connect() as sql:
                sql.execute("DELETE FROM metadata WHERE key=?", (
                    _opaque_key(row._core_notice_cover_document_ref),
                ))
            page = search_public_notice_c(db, "loup")
            assert len(page.hits) == 1 and page.hits[0].document == doc
            return doc, fact.epoch, row.thumbnail_url
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

    seed_job = pool.submit(run_seed)
    deadline = monotonic() + 10

    def close_stage():
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
        state["document"], state["epoch"], state["url"] = seed_job.result()
        start_stage(False)

    def start_stage(offline):
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
            close_stage()
            if not offline:
                # Fresh Storage, CoverLoader and N2, same N3 directory.
                start_stage(True)
                return
            report["checks"]["P0_6366_real_c_n2_n3_n4_restart_offline"] = True
            cleanup()
            window.present()
            later(450, complete)

        later(40, guard(verify))

    later(40, guard(after_seed))
