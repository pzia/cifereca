"""Physical GTK Recommendations from the retained public C, without supplier I/O.

The same production ranking, Core cursor, public host receipt, list factory
and detail panel receive an exact C. The surrounding plugin probe uses an
isolated GTK host and synthetic catalogue/reading-history fixtures.
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

import httpx
from gi.repository import Gdk
from PIL import Image

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_recommendation_known import known_public_c_binding_facts
from cifereca.core.notice_cover_repository import _opaque_key, is_notice_cover_document
from cifereca.core.search_orchestration import _mirror_remote_results, _public_rows_from_response
from cifereca.gui.book_widgets import BookListCell, CoverView
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.storage import Storage
import recommendation_service


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def verify_recommendation_c(window, view, *, later, capture, report, complete):
    owner = view.selected_identity_id()
    assert owner == window.service.current_identity.identity_id
    assert owner == "alice"
    gtk_thread = get_ident()
    pool = ThreadPoolExecutor(max_workers=1)
    temp = TemporaryDirectory(prefix="cifereca-recommendation-c-")
    root = Path(temp.name)
    filename = root / "notice.sqlite"
    original_loader = window.covers
    original_n2 = getattr(window, "document_covers", None)
    state = {}
    gets = []
    url = "https://covers.example.org/recommendation-c.png"
    title = "Même titre"
    author = "Même auteur"
    pixels = (75, 120, 185)
    png = BytesIO()
    Image.new("RGB", (400, 600), pixels).save(png, format="PNG")
    body = png.getvalue()

    def seed():
        assert get_ident() != gtk_thread
        db = Storage(filename)
        try:
            row = SearchResult(
                id="fixture-recommendation-c", record_id="fixture-c-1",
                docbase="digital-books", medium="digital", title=title, author=author,
                type="book", format="EPUB", publisher=None, published_at=None,
                description=None, thumbnail_url=url, friendly_url=None, scenario="search",
                source_provider="fixture-c", source_credential="__public__",
                source_collection="public_catalog", source_account_id=None,
                owned=False, downloadable=False, download_url=None, metadata={},
            )
            rows = _public_rows_from_response(
                ProviderSearchResponse(
                    results=[row], public_notices=(PublicSearchNotice(row),),
                ), NS(provider_id="fixture-c", credential_id="__public__",
                      credentials={}, _accounts=[]),
            )
            assert len(rows) == 1
            assert _mirror_remote_results(
                NS(storage=db), rows, identity_id=owner,
                require_public_cover_witness=True,
            ) == 1
            saved = rows[0]
            ref = saved._core_notice_canonical_cover_document_ref
            c = read_consolidated_notice_cover(db, ref)
            assert c is not None and [candidate.url for candidate in c.candidates] == [url]
            with db._lock, db._connect() as sql:
                sql.execute("DELETE FROM metadata WHERE key=?", (
                    _opaque_key(saved._core_notice_cover_document_ref),
                ))
            assert db.entitlements() == [] and db.acquisitions() == []
            return ref, c.epoch
        finally:
            db.close()

    def close_stage():
        if state.pop("attached", False):
            view._ranked = []
            view._render()
            window.covers = original_loader
            window.document_covers = original_n2
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
            assert monotonic() < deadline, "recommendation C worker timeout"
            later(40, guard(lambda: await_job(job, done, deadline)))
            return
        done(job.result())

    def start_stage(offline):
        label = "offline" if offline else "cold"
        report["recommendation_c_stage"] = label + "_rank"
        ref, epoch = state["facts"]
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

        def read(doc):
            assert get_ident() != gtk_thread
            reads.append(doc)
            return cached_notice_cover_snapshot(NS(storage=db), doc)

        n2 = state["n2"] = DocumentCoverService(
            loader, cached_source_reader=read,
            cached_source_scope=is_notice_cover_document,
        )

        def rank():
            assert get_ident() != gtk_thread
            broker = CatalogBrokerImpl(NS(storage=db))

            class OfflineCatalogue:
                known_public_notices = broker.known_public_notices
                def searchable_providers(self):
                    return []
                def search(self, *_a, **_kw):
                    raise AssertionError("no provider session")
                search_progressive = search

            library = NS(library_entries=lambda **_kw: [
                {"title": "Autre livre", "author": author},
            ])
            # Deterministic fixture query; the actual ranker and C broker
            # execute without any search session or LLM.
            with patch.object(recommendation_service, "build_queries",
                              return_value=("Même", "titre")):
                result = recommendation_service.compute_recommendations(
                    library, OfflineCatalogue(), owner,
                    exclude_owned=False, max_queries=2,
                )
            assert len(result.ranked) == 1
            ranked = result.ranked[0]
            assert len(ranked.notices) == 1
            assert ranked.notices[0].item is ranked.item
            assert not ranked.notices[0].has_source_coordinates
            assert result.network_batches == result.cache_batches == 0
            assert result.catalogue_observations == ()
            assert known_public_c_binding_facts(ranked.item) == (ref, epoch)
            assert db.entitlements() == [] and db.acquisitions() == []
            return ranked

        def show(ranked):
            report["recommendation_c_stage"] = label + "_list"
            state["attached"] = True
            page = view._master._page
            page.clear_rows()
            window.covers, window.document_covers = loader, n2
            # The parent probe previously overrides filtering and keys for its
            # duplicate-occurrence test. Restore the actual plugin methods.
            view.__dict__.pop("_matches", None)
            view.__dict__.pop("_candidate_key", None)
            view._score.set_value(0)
            query = view._query_controls
            query.entry.set_text("")
            query.provider.set_values(())
            query.languages.set_values(())
            query.types.set_values(())
            query.scope.set_selected(0)
            query.field.set_selected(0)
            view._ranked = [ranked]
            view._reload_providers((("fixture-c", "Catalogue témoin"),))
            view._reload_languages(view._ranked)
            view._reload_document_choices()
            view._render()
            assert page.store.get_n_items() == 1
            row = page.store.get_item(0)
            assert row._cover_document_ref == ref
            assert row._cover_plugin_c_receipt.minimum_revision == epoch
            assert row.thumbnail_url is None
            later(80, guard(lambda: check(page, row, "list", monotonic() + 8)))

        def check(page, row, variant, deadline):
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
                assert monotonic() < deadline, "recommendation C N4 timeout"
                later(40, guard(lambda: check(page, row, variant, deadline)))
                return
            cover = covers[0]
            assert cover._loader is loader
            assert cover._request.source_uri == url and cover._request.still_current
            texture = cover.picture.get_paintable()
            with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                assert image.convert("RGB").getpixel((0, 0)) == pixels
            assert reads == [ref] and gets == ["GET"]
            capture("10-c-recommendation-" + label + "-" + variant)
            if variant == "list":
                report["recommendation_c_stage"] = label + "_detail"
                page.selection.set_selected(0)
                assert page.selection.get_selected_item() is row
                later(80, guard(lambda: check(page, row, "detail", monotonic() + 8)))
                return
            stats = loader.stats()
            assert stats["ui_applied"] >= 2 and stats["ui_failed"] == 0
            assert stats["download_ok"] == (0 if offline else 1)
            report["notice_c_recommendation_" + label] = {
                "views": 2, "sqlite_reads": len(reads),
                "image_gets_total": len(gets), "gtk_applied": stats["ui_applied"],
            }
            if not offline:
                close_stage()
                start_stage(True)
                return
            verify_profile_aba(cover, row)

        def verify_profile_aba(cover, row):
            """Dispatch a real warm N3 completion after revoking its GTK lease."""
            from cifereca.gui.plugin_public_c_guard import plugin_public_c_detail_current
            from cifereca.gui.plugin_public_c_binding import (
                issue_public_c_binding, public_c_binding_revision,
            )

            service = window.service
            receipt = row._cover_plugin_c_receipt
            current = lambda: plugin_public_c_detail_current(receipt.view, row, receipt)
            assert current()
            before = loader.stats()
            before_n2 = n2.stats()
            paint_receipts = []
            # CoverView always queues the N3 completion through GLib.idle_add,
            # even for a synchronous warm-cache delivery. Revoke before the
            # GTK main loop can consume this original A completion.
            cover.bind_document(
                n2, ref, variant="detail", minimum_revision=epoch,
                is_current=current,
                on_texture_applied=lambda *_: paint_receipts.append("old"),
            )
            original_request = cover._request
            assert original_request.still_current and original_request.source_uri == url
            service._account_context_epoch += 1
            service.current_identity.identity_id = "bob"
            assert not current()
            service.current_identity.identity_id = owner
            service._account_context_epoch += 1
            assert not current()

            def rejected(deadline):
                after = loader.stats()
                if after["ui_stale"] <= before["ui_stale"]:
                    assert monotonic() < deadline, "late A cover callback timeout"
                    later(40, guard(lambda: rejected(deadline)))
                    return
                assert cover.picture.get_paintable() is None
                assert cover.placeholder.get_visible() and not cover.spinner.get_visible()
                assert after["ui_applied"] == before["ui_applied"]
                assert not paint_receipts
                # A new viewer may get the same PUBLIC C decision and N3
                # media, but only with a freshly issued viewer lease.
                fresh("bob", "B", lambda: fresh(owner, "A", finished))

            def fresh(identity, tag, done):
                service._account_context_epoch += 1
                service.current_identity.identity_id = identity
                bound = issue_public_c_binding(
                    window, view._ranked[0].item, identity_id=identity,
                )
                assert bound is not None and bound[0] == ref
                binding = bound[1]
                live = lambda: public_c_binding_revision(window, ref, binding) == epoch
                assert live() and not current()
                cover.bind_document(
                    n2, ref, variant="detail", minimum_revision=epoch,
                    is_current=live,
                    on_texture_applied=lambda *_: paint_receipts.append(tag),
                )

                def painted(deadline):
                    if not paint_receipts or paint_receipts[-1] != tag:
                        assert monotonic() < deadline, "new viewer cover timeout"
                        later(40, guard(lambda: painted(deadline)))
                        return
                    assert live() and cover._request.still_current
                    assert cover._request.source_uri == url
                    assert cover.get_mapped() and not cover.placeholder.get_visible()
                    texture = cover.picture.get_paintable()
                    assert isinstance(texture, Gdk.Texture)
                    with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                        assert image.convert("RGB").getpixel((0, 0)) == pixels
                    done()

                later(40, guard(lambda: painted(monotonic() + 8)))

            def finished():
                assert paint_receipts == ["B", "A"]
                assert reads == [ref] and gets == ["GET"]
                assert n2.stats()["n1_selected_count"] == before_n2["n1_selected_count"]
                assert loader.stats()["download_ok"] == before["download_ok"]
                assert service.current_identity.identity_id == owner and not current()
                report["checks"]["P0_6168_public_gtk_aba_late_paint_revoked"] = True
                report["recommendation_c_stage"] = "complete"
                report["checks"]["P0_6366_recommendations_ranked_c_gtk_restart_offline"] = True
                cleanup()
                complete()

            later(40, guard(lambda: rejected(monotonic() + 8)))

        job = pool.submit(rank)
        await_job(job, show, monotonic() + 10)

    def seeded(facts):
        state["facts"] = facts
        # The probe's synthetic service implements actual CoreUI epochs.
        window.service._account_context_epoch = 7
        window.service._source_context_epoch = 9
        start_stage(False)

    report["recommendation_c_stage"] = "seed"
    job = pool.submit(seed)
    later(40, guard(lambda: await_job(job, seeded, monotonic() + 10)))
