"""Book-detail-only GTK acceptance of one Core public C across six views.

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
    """One retained C, two actual Origins, six real GTK list/detail views.

    Supplier envelopes, completed deliveries and borrower occurrences are
    synthetic. Core writers, C cursor, ranking, public GTK plugin host,
    N2/N3/N4 and the five native pages are production. The plugin widget
    is temporarily mounted in the fixture MainWindow, not registered.
    No provider call or rights action is performed.
    """
    from concurrent.futures import ThreadPoolExecutor
    from io import BytesIO
    from tempfile import TemporaryDirectory
    from threading import get_ident
    from types import SimpleNamespace as NS
    from dataclasses import replace
    from unittest.mock import patch
    import sys

    import httpx
    from gi.repository import Gtk
    from PIL import Image
    from cifereca.core.canonical_publication import publish_completed_acquisition
    from cifereca.core.source_convergence import SourceKey
    from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
    from cifereca.core.notice_c_local_origin_projection import project_local_origin_public_c
    from cifereca.core.notice_cover_repository import (
        evict_notice_cover, read_notice_cover, is_notice_cover_document,
    )
    from cifereca.core.notice_c_borrower_public_projection import (
        attach_borrower_public_c, borrower_public_c_receipt,
    )
    from cifereca.core.notice_c_public_search import search_public_notice_c
    from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
    from cifereca.core.notice_c_recommendation_known import known_public_c_binding_facts
    from cifereca.brokers import CatalogBrokerImpl
    from cifereca.gui.plugin_ui_host import _UiHost
    from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor
    from cifereca.core.search_orchestration import _mirror_remote_results, _public_rows_from_response
    from cifereca.gui.book_widgets import BookListCell, CoverView
    from cifereca.gui.cover_loader import CoverLoader
    from cifereca.gui.document_cover_service import DocumentCoverService
    from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
    from cifereca.gui.query_model import QueryState
    from cifereca.models import HistoryItem, Loan, SearchResult
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
    original_search_query = window.search_controls.state()
    original_route = window.view_stack.get_visible_child_name()
    original_loans = list(window.service.last_loans)
    original_history = list(window.service.last_history)
    original_loan_filter = window.loan_filter
    original_loan_projection = getattr(window, "_loan_cover_projection", None)
    original_displayed_loan = getattr(window, "_displayed_loan", None)
    account_epoch = window.service._account_context_epoch
    source_epoch = window.service._source_context_epoch
    state = {}
    plugin_dir = str(Path(__file__).resolve().parents[1] / "src/plugins/recommendations")
    sys.path.insert(0, plugin_dir)
    try:
        import recommendation_service
        from recommendations import RecommendationsPlugin
    except BaseException:
        sys.path.remove(plugin_dir)
        pool.shutdown(wait=True, cancel_futures=True)
        tmp.cleanup()
        raise
    routes = ("library", "recent", "search", "loans", "history", "recommendations")
    recommendation_route = "notice-c-recommendations-fixture"
    report["local_c_stage"] = "seed"
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
            for source in docs:
                observation = read_notice_cover(db, source)
                assert observation is not None
                assert evict_notice_cover(db, source, expected_epoch=observation.epoch)
                assert read_notice_cover(db, source) is None
            assert db.entitlements() == [] and db.acquisitions() == []
            return doc, c.epoch, result.artifact_id, result.placement_id, book
        finally:
            db.close()

    def cleanup_stage():
        if state.pop("attached", False):
            # Revoke the fixture bindings before restoring the normal loader.
            plugin = state.pop("recommendations_plugin", None)
            widget = state.pop("recommendations_widget", None)
            if plugin is not None:
                plugin.deactivate()
            if widget is not None:
                window.view_stack.remove(widget)
            for page in (window.library_page, window.recent_page, window.search_page,
                         window.loan_page, window.history_page):
                if page._narrow_master_detail and page._narrow_detail_visible:
                    page.detail_back.emit("clicked")
                page.clear_rows()
                page.detail.empty("Sélectionnez un livre", "")
            window._populate_local_search([])
            window._library_objects = {}
            window._recent_objects = {}
            window._library_entries_cache = original_library
            window._recent_entries_cache = original_recent
            window.library_controls.set_state(original_query, notify=False)
            window.search_controls.set_state(original_search_query, notify=False)
            window.covers, window.document_covers = original_covers, original_n2
            window.loan_filter = original_loan_filter
            window._loan_cover_projection = original_loan_projection
            window._displayed_loan = original_displayed_loan
            window._populate_loans(original_loans)
            window.service.last_history = original_history
            window._populate_history(original_history)
            # A narrow detail can change sidebar visibility and pane allocation.
            # Restore the prior route before the normal XTEST scenario resumes.
            window.navigate(original_route)
        for name in ("n2", "loader", "db"):
            value = state.pop(name, None)
            if value is not None:
                value.close()

    def cleanup():
        cleanup_stage()
        pool.shutdown(wait=True, cancel_futures=True)
        tmp.cleanup()
        if plugin_dir in sys.path:
            sys.path.remove(plugin_dir)

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
        report["local_c_stage"] = "offline_project" if offline else "cold_project"
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
            # Reuse these exact origins for the other view families rather
            # than constructing independently seeded, equal-looking C rows.
            known = merge_public_notice_c_known_rows(
                [], search_public_notice_c(db, title, group_by_document=True),
                identity_id=owner,
            )
            assert len(known) == 1 and known[0]["_cover_document_ref"] == doc
            assert known[0]["metadata"]["notice_c_epoch"] == epoch
            common = dict(
                provider="fixture-branch", medium="digital", record_id="edition-a",
                title=title, author="Autrice témoin", thumbnail_url=None,
                source_provider="fixture-c-a", source_credential="fixture-card",
                source_account_id="fixture-buyer", identity_id=owner,
            )
            loan = Loan(id="fixture-loan", **common)
            history = HistoryItem(id="fixture-history", **common,
                                  borrowed_at="2026-10-01", returned_at="2026-10-15")
            descriptor = ProviderDescriptor(
                id="fixture-c-a", display_name="Catalogue témoin", description="Fixture",
                state="ready", capabilities=ProviderCapabilities(),
                borrower_public_edition_branches_v1=(
                    ("fixture-branch", "digital", "digital-books", None),
                ),
            )
            kwargs = dict(identity_id=owner, account_epoch=account_epoch,
                          source_epoch=source_epoch, descriptors=(descriptor,))
            assert attach_borrower_public_c(db, [loan, history], **kwargs) == 2
            for item in (loan, history):
                receipt = borrower_public_c_receipt(item, identity_id=owner)
                assert receipt.document == doc and receipt.epoch == epoch
            assert attach_borrower_public_c(
                db, [replace(history, identity_id="fixture-foreign")], **kwargs,
            ) == 0
            broker = CatalogBrokerImpl(NS(storage=db))

            class OfflineCatalogue:
                known_public_notices = broker.known_public_notices
                def searchable_providers(self):
                    return []
                def search(self, *_a, **_kw):
                    raise AssertionError("unexpected supplier search")
                search_progressive = search

            library = NS(library_entries=lambda **_kw: [
                {"title": "Autre livre", "author": "Autrice témoin"},
            ])
            with patch.object(recommendation_service, "build_queries",
                              return_value=("Notice", "témoin")):
                ranking = recommendation_service.compute_recommendations(
                    library, OfflineCatalogue(), owner, exclude_owned=False, max_queries=2,
                )
            ranked, = ranking.ranked
            assert known_public_c_binding_facts(ranked.item) == (doc, epoch)
            assert ranking.network_batches == ranking.cache_batches == 0
            assert ranking.catalogue_observations == ()
            return local, recent, known, loan, history, ranked

        def show(rows):
            state["attached"] = True
            window.covers, window.document_covers = loader, n2
            window.library_controls.set_state(QueryState(), notify=False)
            window.library_page.clear_rows()
            window.recent_page.clear_rows()
            window._library_entries_cache = [rows[0]]
            window._recent_entries_cache = [rows[1]]
            state["projected_rows"] = rows
            show_route("library")

        label = "offline" if offline else "cold"
        route_timings = {}
        route_started = {}

        def show_route(route):
            route_started[(route, "list")] = monotonic()
            report["local_c_stage"] = label + "_" + route + "_route"
            if route == "search":
                window.search_controls.set_state(
                    QueryState(text=title, field="all", scope="all"), notify=False,
                )
                window.navigate(route)
                window._populate_local_search(state["projected_rows"][2])
            elif route == "loans":
                # Do not let the earlier local-origin fixture be interpreted
                # as an attested EPUB of the synthetic borrower occurrence.
                window._loan_cover_projection = None
                window._library_entries_cache = []
                window.loan_filter = "all"
                window._populate_loans([state["projected_rows"][3]])
                window.navigate(route)
            elif route == "history":
                history = state["projected_rows"][4]
                window.service.last_history = [history]
                window._populate_history([history])
                window.navigate(route)
            elif route == "recommendations":
                plugin = RecommendationsPlugin().activate(NS(config={"exclude_owned": False}))
                state["recommendations_plugin"] = plugin
                widget = plugin.build(_UiHost(window))
                state["recommendations_widget"] = widget
                view = plugin._view
                view._start_refresh = lambda: None
                view._score.set_value(0)
                query = view._query_controls
                query.entry.set_text("")
                query.provider.set_values(())
                query.languages.set_values(())
                query.types.set_values(())
                query.scope.set_selected(0)
                query.field.set_selected(0)
                assert view.selected_identity_id() == owner
                view._ranked = [state["projected_rows"][5]]
                view._reload_providers((("fixture-c-a", "Catalogue A"),
                                        ("fixture-c-b", "Catalogue B")))
                view._reload_languages(view._ranked)
                view._reload_document_choices()
                view._render()
                window.view_stack.add_named(widget, recommendation_route)
                window.view_stack.set_visible_child_name(recommendation_route)
            else:
                window.navigate(route)
            # Native Gtk.Stack and Paned allocate asynchronously on a route
            # transition. Wait for the actual visible page before selecting.
            later(80, guard(lambda: show_list(route)))

        def show_list(route):
            report["local_c_stage"] = label + "_" + route + "_list"
            page = (
                state["recommendations_plugin"]._view._master._page
                if route == "recommendations"
                else window.loan_page if route == "loans"
                else getattr(window, route + "_page")
            )
            if page._narrow_master_detail and page._narrow_detail_visible:
                page.detail_back.emit("clicked")
            page.selection.set_selected(0xFFFFFFFF)
            assert page.store.get_n_items() == 1
            row = page.store.get_item(0)
            assert row._cover_document_ref == doc
            if route in ("library", "recent"):
                assert row._cover_public_c_link.epoch == epoch
            elif route == "search":
                assert row._cover_public_c_epoch == epoch
            elif route in ("loans", "history"):
                assert row._cover_borrower_public_c_receipt.epoch == epoch
            else:
                assert row._cover_plugin_c_receipt.minimum_revision == epoch
            assert row.thumbnail_url is None
            deadline = monotonic() + 8
            later(40, guard(lambda: wait_texture(route, page, row, "list", deadline)))

        def wait_texture(route, page, row, variant, deadline):
            assert window.view_stack.get_visible_child_name() == (
                recommendation_route if route == "recommendations" else route
            )
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
            # Wall clock from route/selection to observed N4, including GTK
            # scheduling and polling. Distinct from worker/decoder percentiles.
            route_timings[route + "/" + variant] = round(
                (monotonic() - route_started[(route, variant)]) * 1000, 2,
            )
            capture("00-notice-c-" + route + "-" + variant + "-" + label)
            if variant == "list":
                report["local_c_stage"] = label + "_" + route + "_detail"
                route_started[(route, "detail")] = monotonic()
                page.selection.set_selected(0)
                assert page.selection.get_selected_item() is row
                # The actual production selection signal renders the detail.
                # In narrow mode, require that it opens the real detail pane.
                if page._narrow_master_detail:
                    assert page._narrow_detail_visible
                later(80, guard(lambda: wait_texture(
                    route, page, row, "detail", monotonic() + 8,
                )))
                return
            if page._narrow_master_detail and page._narrow_detail_visible:
                page.detail_back.emit("clicked")
            index = routes.index(route)
            if index < len(routes) - 1:
                show_route(routes[index + 1])
                return
            stats = loader.stats()
            assert stats["ui_applied"] >= 12 and stats["ui_failed"] == 0
            assert stats["download_ok"] == (0 if offline else 1)
            decisions = n2.stats()
            # A cold N2 is rebuilt even with warm N3. Do not infer a
            # documentary HIT from an image file or a texture.
            assert decisions["n2_register_new"] >= 1
            assert decisions["n1_selected_count"] >= 1
            assert decisions["n2_hit"] >= 1
            assert stats["ui_decode_recent_count"] >= 1
            assert stats["n3_url_ok_recent_count"] == (0 if offline else 1)
            assert len(route_timings) == 12
            # Report only fixed numeric metrics; no source, URI, path,
            # document key or account. Empty samples have no percentile.
            def select_metrics(values, prefixes):
                return {
                    key: value for key, value in values.items()
                    if key.startswith(prefixes) and type(value) in (int, float)
                }

            report["notice_c_cover_metrics_" + label] = {
                "schema": "cifereca.cover-six-views/1",
                "fixture": "one_c_six_views_mock_transport",
                "n1": select_metrics(decisions, ("n1_",)),
                "n2": select_metrics(decisions, ("n2_",)),
                "n3": {
                    **select_metrics(stats, ("n3_url_", "n3_epub_", "preview_")),
                    **{key: stats[key] for key in (
                        "cache_hit_valid", "download_ok", "download_failed",
                    )},
                },
                "n4": select_metrics(stats, ("ui_decode_", "ui_applied", "ui_failed", "ui_stale")),
                "route_to_observed_texture_ms": route_timings,
                "image_gets_total": len(gets),
            }
            assert window.service._account_context_epoch == account_epoch
            assert window.service._source_context_epoch == source_epoch
            report["notice_c_local_views_" + label] = {
                "views": 4, "sqlite_reads": len(reads),
                "image_gets_total": len(gets), "gtk_applied": stats["ui_applied"],
            }
            report["notice_c_shared_six_views_" + label] = {
                "views": 12, "routes": list(routes), "same_document": True,
                "same_epoch": True, "sqlite_reads": len(reads),
                "image_gets_total": len(gets), "gtk_applied": stats["ui_applied"],
            }
            cleanup_stage()
            if not offline:
                start_stage(True)
            else:
                report["checks"]["P0_6366_mainwindow_library_recent_restart_offline"] = True
                report["checks"]["P0_4980_same_c_six_physical_views_restart_offline"] = True
                report["local_c_stage"] = "complete"
                cleanup()
                window.present()
                complete()

        stamp_value = 1794900000
        future = pool.submit(project)
        await_job(future, show, monotonic() + 10)

    def seeded(facts):
        state["facts"] = facts
        start_stage(False)

    later(40, guard(lambda: await_job(job, seeded, monotonic() + 10)))
