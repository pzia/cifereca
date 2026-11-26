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


def verify_local_notice_c_views(window, later, capture, report, complete, *, late_audit=None,
                                settled_capture=False):
    """One retained C, three completed test Origins, six real GTK list/detail views.

    One source is the unmodified recorded Numilog HTML through its actual
    parser/session; the other envelopes, deliveries and borrower occurrences
    are synthetic. Core writers, C cursor, ranking, public GTK plugin host,
    N2/N3/N4 and the five native pages are production. The plugin widget
    is temporarily mounted in the fixture MainWindow, not registered.
    No provider call or rights action is performed.
    """
    report["local_c_stage"] = "setup"
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
    url = "https://couverture.numilog.com/9782702194287_GRANDE.jpg"
    title = "Les Enfants du Pas du Loup"
    author = "Marie-Bernadette Dupuy"
    recorded_provider = "numilog-catalog"
    recorded_record = "numilog:public:1947941"
    pixels = (47, 108, 162)
    png = BytesIO()
    Image.new("RGB", (400, 600), pixels).save(png, format="PNG")
    body = png.getvalue()
    replacement_pixels = (170, 45, 82)
    replacement_png = BytesIO()
    Image.new("RGB", (400, 600), replacement_pixels).save(replacement_png, format="PNG")
    replacement_body = replacement_png.getvalue()

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
            # Use the existing recorded web response, not a live request.
            from notice_c_cover_contract import PLUGIN, URL_SEARCH
            sys.path.insert(0, str(PLUGIN))
            session = None
            try:
                import cifereca_numilog_public_provider as numilog
                from cifereca_numilog_public import parse_public_search
                parsed = parse_public_search(
                    (PLUGIN / "tests/fixtures/observed_search-loup-0.html").read_text(
                        encoding="utf-8"), URL_SEARCH,
                )
                calls = []
                session = numilog.NumilogPublicSession(
                    credential_id="__public__", label="Recorded catalogue",
                    credentials={}, options={},
                )
                session.connect()
                with patch.object(numilog, "fetch_public_search",
                                  side_effect=lambda *_a, **_k: calls.append(1) or parsed):
                    response = session.search_with_response_facets("loup", scope="catalog")
                parsed_rows = _public_rows_from_response(response, session)
                assert calls == [1] and len(parsed_rows) == 1
                actual = parsed_rows[0]
                assert (actual.source_provider, actual.record_id, actual.title,
                        actual.author, actual.thumbnail_url) == (
                            recorded_provider, recorded_record, title, author, url)
                assert actual.publisher == "Calmann-Lévy"
                assert actual.owned is actual.downloadable is False
                assert actual.download_url is actual.source_account_id is None
            finally:
                if session is not None:
                    session.close()
                sys.path.remove(str(PLUGIN))

            admitted = []
            for provider, record in (("fixture-c-a", "edition-a"),
                                     ("fixture-c-b", "edition-b"),
                                     (recorded_provider, recorded_record)):
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
                if provider == recorded_provider:
                    # Keep the exact Core-produced row, not a rebuilt DTO.
                    admitted.append(actual)
                    continue
                metadata = {
                    "subjects": ["Astronomie" if provider == "fixture-c-a" else "Cuisine"],
                    "genre": "Roman" if provider == "fixture-c-a" else "Essai",
                }
                if provider == "fixture-c-a":
                    evidence = {"source": provider + ".fixture", "scope": "search",
                                "freshness": "search_response"}
                    metadata.update(
                        series=["Cycle témoin"], languages=["ja"],
                        bibliography_evidence={
                            "series": {**evidence, "source_field": "Search.series",
                                       "confidence": "linked_search_relation"},
                            "languages": {**evidence, "source_field": "dc.language",
                                          "confidence": "explicit_record_field",
                                          "raw_tags": ["ja"]},
                        },
                    )
                row = SearchResult(
                    id=record, record_id=record, docbase="digital-books",
                    medium="digital", title=title, author=author,
                    type="book", format="PDF", publisher=None, published_at=None,
                    description=None, thumbnail_url=url, friendly_url=None,
                    scenario="search", source_provider=provider,
                    source_credential="__public__", source_collection="public_catalog",
                    source_account_id=None, owned=False, downloadable=False,
                    download_url=None, metadata=metadata,
                )
                response = ProviderSearchResponse(
                    results=[row], public_notices=(PublicSearchNotice(row),),
                )
                rows = _public_rows_from_response(response, NS(
                    provider_id=provider, credential_id="__public__",
                    credentials={}, _accounts=[],
                ))
                assert len(rows) == 1
                admitted.extend(rows)
            assert _mirror_remote_results(
                NS(storage=db), admitted, identity_id=owner,
                require_public_cover_witness=True,
            ) == 3
            doc = actual._core_notice_canonical_cover_document_ref
            assert all(row._core_notice_canonical_cover_document_ref == doc for row in admitted)
            c = read_consolidated_notice_cover(db, doc)
            assert len(c.sources) == 3 and {x.url for x in c.candidates} == {url}
            member, = [source for source in c.sources if source.provider_id == recorded_provider]
            assert member.public_bibliography.record_id == recorded_record
            assert member.public_bibliography.publisher == actual.publisher
            assert member.public_bibliography.published_at == actual.published_at
            ref, = member.public_web_references
            assert ref.url == actual.friendly_url
            for source in (row._core_notice_cover_document_ref for row in admitted):
                observation = read_notice_cover(db, source)
                assert observation is not None
                assert evict_notice_cover(db, source, expected_epoch=observation.epoch)
                assert read_notice_cover(db, source) is None
            assert db.entitlements() == [] and db.acquisitions() == []
            # An independent source deliberately shares only the image URL. It has
            # neither a matching Origin nor a strong edition relation.
            independent = SearchResult(
                id="independent-c", record_id="independent-c", docbase="digital-books",
                medium="digital", title="Atlas indépendant", author="Géographe",
                type="book", format="PDF", publisher=None, published_at=None,
                description=None, thumbnail_url=url, friendly_url=None,
                scenario="search", source_provider="fixture-c-other",
                source_credential="__public__", source_collection="public_catalog",
                source_account_id=None, owned=False, downloadable=False,
                download_url=None, metadata={},
            )
            separate = _public_rows_from_response(
                ProviderSearchResponse(results=[independent],
                    public_notices=(PublicSearchNotice(independent),)),
                NS(provider_id="fixture-c-other", credential_id="__public__",
                   credentials={}, _accounts=[]),
            )
            assert len(separate) == 1
            assert _mirror_remote_results(NS(storage=db), separate, identity_id=owner,
                                          require_public_cover_witness=True) == 1
            other_doc = separate[0]._core_notice_canonical_cover_document_ref
            other = read_consolidated_notice_cover(db, other_doc)
            assert other_doc != doc and other is not None
            assert len(other.sources) == 1 and {x.url for x in other.candidates} == {url}
            assert len(read_consolidated_notice_cover(db, doc).sources) == 3
            other_source = read_notice_cover(db, separate[0]._core_notice_cover_document_ref)
            assert other_source is not None
            assert evict_notice_cover(db, other_source.document, expected_epoch=other_source.epoch)
            assert db.entitlements() == [] and db.acquisitions() == []
            return doc, c.epoch, result.artifact_id, result.placement_id, book, other_doc, other.epoch
        finally:
            db.close()

    def cleanup_stage():
        matrix = state.pop("matrix_widget", None)
        if matrix is not None:
            for child in walk(matrix):
                if isinstance(child, CoverView):
                    child.reset()
            window.view_stack.remove(matrix)
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
        cache_patch = state.pop("epub_cache_patch", None)
        if cache_patch is not None:
            cache_patch.stop()
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
        doc, epoch, artifact, placement, book, other_doc, other_epoch = state["facts"]
        db = state["db"] = Storage(filename)
        loader = state["loader"] = CoverLoader(max_workers=2)
        loader.root = root / "images"
        loader.root.mkdir(exist_ok=True)
        loader.preview_root = loader.root / "previews-v1"
        loader.preview_root.mkdir(exist_ok=True)
        reads = []
        fallback_images = tuple(f"https://covers.example.test/fallback-{i}.png"
                                for i in (1, 2, 3))
        fallback_gets = []

        def serve(req):
            address = str(req.url)
            if address in fallback_images:
                assert offline and state.get("catalogue_fallback") and req.method == "GET"
                fallback_gets.append(address)
                status = (404, 410, 200)[fallback_images.index(address)]
                return httpx.Response(status, content=body if status == 200 else b"",
                                      headers={"content-type": "image/png"}, request=req)
            gets.append(str(req.method))
            recovery = state.get("recover_n3") is True
            assert (not offline or recovery) and req.method == "GET" and str(req.url) == url
            return httpx.Response(200, content=replacement_body if recovery else body,
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
                "title": title, "author": author, "media_format": "pdf",
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
                assert len(link.source_refs) == 3
            # Reuse these exact origins for the other view families rather
            # than constructing independently seeded, equal-looking C rows.
            known = merge_public_notice_c_known_rows(
                [], search_public_notice_c(db, title, group_by_document=True),
                identity_id=owner,
            )
            assert len(known) == 1 and known[0]["_cover_document_ref"] == doc
            assert known[0]["metadata"]["notice_c_epoch"] == epoch
            from cifereca.core.notice_c_known_projection import known_public_alternative_facts
            observed, = [hit for hit in known_public_alternative_facts(known[0])
                         if hit.provider_id == recorded_provider]
            assert observed.record_id == recorded_record and observed.publisher == "Calmann-Lévy"
            from cifereca.core.notice_c_web_choices import read_public_web_choices, resolve_public_web_choice
            menu = read_public_web_choices(db, doc)
            choice, = menu.choices
            assert choice.provider_id == recorded_provider and menu.epoch == epoch
            assert resolve_public_web_choice(db, doc, expected_epoch=epoch,
                                             choice_id=choice.choice_id) == (
                "https://www.numilog.com/1947941/les-enfants-du-pas-du-loup.ebook")
            common = dict(
                provider="fixture-branch", medium="digital", record_id="edition-a",
                title=title, author=author, thumbnail_url=None,
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
                {"title": "Autre livre", "author": author},
            ])
            with patch.object(recommendation_service, "build_queries",
                              return_value=("Enfants", "Loup")):
                # Keep the pre-existing six-view ranking corpus isolated:
                # the extra Atlas fixture shares N3, not either query term.
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
            verify_documentary_summary()

        def verify_documentary_summary():
            # Complement the real SQLite/adapter receipt in #1072 by checking
            # the actual mapped GTK label and list with controlled projection
            # packets. These DTOs are presentation fixtures, NOT new rights,
            # supplier evidence, or a second documentary writer.
            from cifereca.core.notice_c_purchase_descriptive_read import (
                PurchaseDescriptiveFact, PurchaseDescriptiveNotice,
            )
            from cifereca.core.notice_c_purchase_library_projection import (
                PurchaseLibraryNotice, PurchaseLibraryStatus,
            )
            from cifereca.plugin_api import SourceReference
            page = window.library_page
            items = []
            for record, provider, book_title in (
                ("edition-a", "fixture-c-a", "Roman documentaire"),
                ("edition-b", "fixture-c-b", "Atlas à actualiser"),
            ):
                scope = ProviderRightsScope(owner, provider, "fixture-card", "fixture-buyer")
                source = SourceReference(scope, "purchases", record, "purchase", "purchase")
                key = "fixture-purchase:" + record
                item = {
                    "key": key, "identity_id": owner, "provider_id": provider,
                    "item_id": record, "source": "private_catalog_observation",
                    "state": "remote_catalog", "local": False,
                    "title": book_title, "author": "", "metadata": {},
                    "possessions": [], "source_refs": [{
                        "identity_id": owner, "provider_id": provider,
                        "credential_id": scope.credential_id,
                        "account_id": scope.account_id, "item_id": record,
                        "kind": "purchase",
                    }],
                }
                if record == "edition-a":
                    fact = PurchaseDescriptiveFact("title", book_title, provider,
                                                  "synthetic", "synthetic", 100)
                    item["_purchase_library_notice"] = PurchaseLibraryNotice(
                        key, source, PurchaseDescriptiveNotice(doc, epoch, 100, (fact,)))
                else:
                    item["_purchase_library_status"] = PurchaseLibraryStatus(
                        key, source, "failed", 1)
                items.append(item)
            expected = ("Enrichissement documentaire (achats qualifiés) : "
                        "1 fiches disponibles · 1 non abouties")
            window._library_entries_cache = items
            window.navigate("library")
            window._render_library(items)

            def check(deadline):
                if not page._documentary_summary.get_mapped():
                    assert monotonic() < deadline, "documentary summary GTK not mapped"
                    later(40, guard(lambda: check(deadline)))
                    return
                assert page.store.get_n_items() == 2
                assert page._documentary_summary.get_text() == expected
                window.library_controls.set_state(
                    QueryState(text="Roman documentaire", field="title"), notify=False)
                window._render_library(items)
                assert page.store.get_n_items() == 1
                assert page._documentary_summary.get_text() == expected
                assert page._documentary_summary.get_mapped()
                capture("00-purchase-documentary-mixed-" + label)
                report["checks"]["P0_1072_mixed_documentary_native_gtk_" + label] = True
                # Restore the original six-view corpus before any cover
                # subscription. Synthetic packets must not enter its metrics.
                page.clear_rows()
                window.library_controls.set_state(QueryState(), notify=False)
                window._library_entries_cache = [state["projected_rows"][0]]
                assert not reads
                show_route("library")

            later(80, guard(lambda: check(monotonic() + 8)))

        label = "offline" if offline else "cold"
        route_timings = {}
        route_started = {}
        narrow_context = {}

        def show_route(route):
            route_started[(route, "list")] = monotonic()
            report["local_c_stage"] = label + "_" + route + "_route"
            if route == "search":
                window.search_controls.set_state(
                    QueryState(text=title, field="all", scope="all"), notify=False,
                )
                window.navigate(route)
                # Use the exact retained alternative in the actual local
                # Search widget, not a flattened union of source subjects.
                window.search_controls.set_state(
                    QueryState(text="Cuisine", field="subject", scope="all"), notify=False,
                )
                window._populate_local_search(state["projected_rows"][2])
                assert window.search_page.store.get_n_items() == 1
                window.search_controls.set_state(
                    QueryState(text="Cuisine", field="all", scope="all"), notify=False,
                )
                window._render_search_results()
                assert window.search_page.store.get_n_items() == 1
                assert "Correspondance : Sujet" in window.search_page.store.get_item(0).meta
                report["checks"]["P0_1023_retained_c_match_reason_" + label] = True
                window.search_controls.set_state(
                    QueryState(text="Cuisine Astronomie", field="subject", scope="all"),
                    notify=False,
                )
                window._render_search_results()
                assert window.search_page.store.get_n_items() == 0
                window.search_controls.set_state(
                    QueryState(text=title, field="all", scope="all"), notify=False,
                )
                window._render_search_results()
                assert window.search_page.store.get_n_items() == 1
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
                                        ("fixture-c-b", "Catalogue B"),
                                        (recorded_provider, "Numilog")))
                view._reload_languages(view._ranked)
                view._reload_document_choices()
                # The same independent-source predicates as Search, on
                # the actual shared local filter controls. No recalculation
                # or supplier search; the original rank and C ref survive.
                original_rank = view._ranked[0]
                page = view._master._page
                query.set_state(QueryState(text="Cuisine", field="subject", scope="all"),
                                notify=False)
                view._render()
                assert page.store.get_n_items() == 1
                query.set_state(QueryState(text="Cuisine Astronomie", field="subject", scope="all"),
                                notify=False)
                view._render()
                assert page.store.get_n_items() == 0
                query.set_state(QueryState(), notify=False)
                view._render()
                assert page.store.get_n_items() == 1 and view._ranked[0] is original_rank
                report["checks"]["P0_4761_same_source_filter_parity_" + label] = True
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

        def wait_texture(route, page, row, variant, deadline, revoked=None, settled=False):
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
                later(40, guard(lambda: wait_texture(route, page, row, variant, deadline, revoked)))
                return
            cover = covers[0]
            if settled_capture and revoked is None:
                from notice_c_native_readability import stack_transition_running
                running = stack_transition_running(cover, window, Gtk.Stack)
                # Native Stack CROSSFADE keeps the destination mapped before
                # it is fully composited. Wait for idle and another event-loop
                # interval, then revalidate the same row/texture. Never change
                # production animations or accept an unpainted screenshot.
                if running or not settled:
                    assert monotonic() < deadline, "native capture transition timeout"
                    later(40, guard(lambda: wait_texture(
                        route, page, row, variant, deadline, settled=not running,
                    )))
                    return
            assert cover._loader is loader
            assert cover._request.source_uri == url and cover._request.still_current
            texture = cover.picture.get_paintable()
            with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                assert image.convert("RGB").getpixel((0, 0)) == pixels
            assert reads == [doc] and gets == ["GET"]
            if revoked is not None:
                late_audit.restored(route, variant, cover, revoked)
            else:
                # Wall clock for the original route, not the fault injection.
                route_timings[route + "/" + variant] = round(
                    (monotonic() - route_started[(route, variant)]) * 1000, 2,
                )
                capture("00-notice-c-" + route + "-" + variant + "-" + label)
                if offline and late_audit is not None:
                    def native_rebind():
                        assert page.store.get_item(0) is row
                        if variant == "list":
                            cells = [cell for cell in walk(page)
                                     if isinstance(cell, BookListCell)
                                     and cell.get_mapped() and cell.cover is cover
                                     and cell._bound_key == row.key]
                            cell, = cells
                            cell._thumbnail_changed(row)
                        else:
                            page.selection.set_selected(0xFFFFFFFF)
                            page.selection.set_selected(0)
                            assert page.selection.get_selected_item() is row
                            if page._narrow_master_detail:
                                assert page._narrow_detail_visible

                    def restore(revoked_request):
                        assert reads == [doc] and gets == ["GET"]
                        native_rebind()
                        later(40, guard(lambda: wait_texture(
                            route, page, row, variant, monotonic() + 8, revoked_request,
                        )))

                    report["local_c_stage"] = label + "_" + route + "_" + variant + "_late_aba"
                    late_audit.deliver_late(
                        route, variant, n2, doc, native_rebind,
                        later=later, guard=guard, complete=restore,
                    )
                    return
            if variant == "list":
                report["local_c_stage"] = label + "_" + route + "_detail"
                # Closed geometry witness for the existing 960px acceptance.
                # Record both the actual allocation and the native mode, even
                # if the narrow transition is not entered.
                report.setdefault("notice_c_narrow_geometry_" + label, {})[route] = {
                    "window_width": int(window.get_width()),
                    "page_width": int(page.get_width()),
                    "paned_max": int(page.paned.get_property("max-position")),
                    "narrow": bool(page._narrow_master_detail),
                }
                if page._narrow_master_detail:
                    assert page.page_header.get_visible()
                    assert page.toolbar.get_visible()
                    if route == "search":
                        assert page.header_actions.get_parent() is page
                        assert page.header_actions.get_mapped()
                    narrow_context[route] = {
                        "window_width": window.get_width(),
                        "sidebar_visible": bool(window.sidebar.get_visible()),
                    }
                    if route == "library":
                        # Exercise the actual label and refresh port, without
                        # modifying the documentary data behind the fixture.
                        narrow_context[route]["original_summary"] = page._documentary_summary.get_text()
                        page.set_documentary_summary("1 notice témoin")
                        assert page._documentary_summary.get_visible()
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
            if route in ("search", "recommendations"):
                from gi.repository import Adw
                alternative_rows = [
                    widget for widget in walk(page.detail.content)
                    if isinstance(widget, Adw.ActionRow)
                    and widget.get_title() == "Source 2 · fixture-c-b"
                ]
                assert len(alternative_rows) == 1
                alternative = alternative_rows[0]
                assert alternative.get_mapped()
                subtitle = alternative.get_subtitle()
                assert "Sujets : Cuisine" not in subtitle and "Genre : Essai" not in subtitle
                assert "Astronomie" not in subtitle and "Roman" not in subtitle
                for field, value in (("Sujet", "Cuisine"), ("Genre", "Essai")):
                    qualified, = [
                        widget for widget in walk(page.detail.content)
                        if isinstance(widget, Adw.ActionRow)
                        and widget.get_title() == "Source 2 · fixture-c-b · " + field
                    ]
                    assert qualified.get_mapped()
                    assert qualified.get_subtitle() == value
                    assert "Fournisseur déclaré : fixture-c-b" in (qualified.get_tooltip_text() or "")
                    assert "Référence déclarée : edition-b" in (qualified.get_tooltip_text() or "")
                assert not alternative.has_css_class("detail-metadata-actions")
                # The alternative is an exact retained source summary, not
                # a field-level parser proof or a reconciled canonical value.
                alternative_tooltip = alternative.get_tooltip_text() or ""
                proof_lines = [
                    "Source du champ : Notice C · observation de source",
                    "Fournisseur déclaré : fixture-c-b",
                    "Référence déclarée : edition-b",
                ]
                assert alternative_tooltip.splitlines()[-4:-1] == proof_lines
                assert alternative_tooltip.splitlines()[-1].startswith("Observation déclarée : ")
                observed_rows = [
                    widget for widget in walk(page.detail.content)
                    if isinstance(widget, Adw.ActionRow)
                    and widget.get_title() == "Source 3 · " + recorded_provider
                ]
                observed_row, = observed_rows
                assert observed_row.get_mapped()
                observed_text = observed_row.get_subtitle()
                observed_tooltip = observed_row.get_tooltip_text() or ""
                assert "Éditeur : Calmann-Lévy" in observed_text
                assert "Fournisseur déclaré : " + recorded_provider in observed_tooltip
                assert "Référence déclarée : " + recorded_record in observed_tooltip
                if route == "search":
                    # Check the native action rows made by the same selection
                    # whose DetailPanel and cover are mapped above.
                    for field, value, source_field in (
                        ("Série", "Cycle témoin", "Search.series"),
                        ("Langue", "ja", "dc.language"),
                    ):
                        metadata_rows = [
                            widget for widget in walk(page.detail.content)
                            if isinstance(widget, Adw.ActionRow) and widget.get_title() == field
                        ]
                        assert len(metadata_rows) == 1, "missing or duplicate C provenance row: " + field
                        tooltip = metadata_rows[0].get_tooltip_text() or ""
                        assert tooltip.splitlines()[:4] == [
                            f"{field} : {value}",
                            "Source du champ : fixture-c-a.fixture · " + source_field,
                            "Fournisseur déclaré : fixture-c-a",
                            "Référence déclarée : edition-a",
                        ], "C provenance differs from the primary source: " + field
                        assert "Observation déclarée : " in tooltip
                    state["source_alternative_tooltip_" + label] = alternative_tooltip
                    report["checks"]["P0_3688_search_native_field_provenance_" + label] = True
                    state["source_alternative_" + label] = subtitle
                    state["recorded_alternative_" + label] = (observed_text, observed_tooltip)
                else:
                    assert subtitle == state["source_alternative_" + label]
                    assert alternative_tooltip == state["source_alternative_tooltip_" + label]
                    assert (observed_text, observed_tooltip) == state["recorded_alternative_" + label]
                    report["checks"]["P0_4761_same_source_alternative_search_recommendations_" + label] = True
            if route in narrow_context:
                receipt = narrow_context[route]
                assert page._narrow_master_detail and page._narrow_detail_visible
                assert not page.page_header.get_mapped()
                assert not page.toolbar.get_mapped()
                assert not window.sidebar.get_visible()
                assert page.detail_back.get_mapped()
                if route == "search":
                    assert not page.header_actions.get_mapped()
                if route == "library":
                    assert not page._documentary_summary.get_mapped()
                    page.set_documentary_summary("2 notices témoins")
                    assert not page._documentary_summary.get_visible()
                page.detail_back.emit("clicked")
                assert not page._narrow_detail_visible
                assert page.page_header.get_visible() and page.toolbar.get_visible()
                assert bool(window.sidebar.get_visible()) == receipt["sidebar_visible"]
                assert page.selection.get_selected_item() is row
                if route == "search":
                    assert page.header_actions.get_visible()
                if route == "library":
                    assert page._documentary_summary.get_visible()
                    assert page._documentary_summary.get_text() == "2 notices témoins"
                    page.set_documentary_summary(receipt.pop("original_summary"))
                receipt["returned"] = True
                # Returning preserves selection, so activating that exact row
                # again must reopen it via the native ListView signal, not a
                # second selection notification or a fresh source request.
                def focus_within(widget):
                    focused = window.get_focus()
                    return focused is not None and (
                        focused is widget or focused.is_ancestor(widget)
                    )

                assert focus_within(page.listview), "narrow return focus: " + route
                selection_events = []
                handler = page.selection.connect(
                    "notify::selected-item",
                    lambda *_args: selection_events.append(True),
                )
                try:
                    page.listview.emit("activate", 0)
                    assert page._narrow_detail_visible
                    assert page.selection.get_selected_item() is row
                    assert not selection_events
                    assert not page.page_header.get_visible()
                    assert not page.toolbar.get_visible()
                    assert focus_within(page.detail) or focus_within(page.detail_back), (
                        "narrow reopened detail focus: " + route
                    )
                    assert reads == [doc] and gets == ["GET"]
                    page.detail_back.emit("clicked")
                    assert not page._narrow_detail_visible
                    assert page.selection.get_selected_item() is row
                    assert not selection_events
                    assert focus_within(page.listview), "narrow second return focus: " + route
                finally:
                    page.selection.disconnect(handler)
                receipt["reopened_exact_selection"] = True
                receipt["native_focus_returned"] = True
            elif page._narrow_master_detail and page._narrow_detail_visible:
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
            # A wide geometry is not evidence of the narrow transition.
            report["notice_c_narrow_context_" + label] = narrow_context
            if set(narrow_context) == set(routes):
                assert all(
                    value["returned"] and value["reopened_exact_selection"]
                    and value["native_focus_returned"]
                    for value in narrow_context.values()
                )
                report["checks"]["P0_3684_narrow_six_views_" + label] = True
            # Real GdkPixbuf preview worker, not the Pillow baseline adapter.
            # A single C and a fixed preview size: creation only on cold N3,
            # reuse after restart; samples count executions, not subscribers.
            assert stats["preview_created"] == (0 if offline else 1)
            assert stats["preview_fallback"] == 0
            assert stats["preview_hit"] >= 1
            assert stats["preview_worker_created_recent_count"] == stats["preview_created"]
            assert stats["preview_worker_hit_recent_count"] == stats["preview_hit"]
            assert stats["preview_worker_fallback_recent_count"] == 0
            assert "preview_worker_hit_recent_p95_ms" in stats
            assert ("preview_worker_created_recent_p95_ms" in stats) is (not offline)
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
            if not offline:
                cleanup_stage()
                start_stage(True)
            else:
                # Preserve the completed offline receipt above. The following
                # independent, explicitly online mock phase deletes only N3
                # in scratch space; the existing N2 token stays authoritative.
                verify_warm_n2_cold_n3()

        def verify_warm_n2_cold_n3():
            report["local_c_stage"] = "warm_n2_cold_n3"
            assert reads == [doc] and gets == ["GET"]
            source = loader._path(url)
            assert source.is_file()
            before_n2 = n2.stats()
            before_n3 = loader.stats()
            loader.invalidate(source)
            assert not source.exists()
            state["recover_n3"] = True
            # Two standard live GTK surfaces share the exact C.
            # No hand-built resolution, no direct URL binding.
            matrix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            state["matrix_widget"] = matrix
            list_cover = CoverView(60, 90)
            detail_cover = CoverView(120, 180)
            matrix.append(list_cover)
            matrix.append(detail_cover)
            window.view_stack.add_named(matrix, "notice-c-cache-matrix-fixture")
            window.view_stack.set_visible_child_name("notice-c-cache-matrix-fixture")
            for variant, cover in (("list", list_cover), ("detail", detail_cover)):
                cover.bind_document(n2, doc, variant=variant, minimum_revision=epoch)

            def recovered(deadline):
                covers = (list_cover, detail_cover)
                ready = all(
                    cover.get_mapped()
                    and isinstance(cover.picture.get_paintable(), Gdk.Texture)
                    and not cover.placeholder.get_visible()
                    and not cover.spinner.get_visible()
                    for cover in covers
                )
                if not ready:
                    assert monotonic() < deadline, "N2 warm / N3 cold GTK timeout"
                    later(40, guard(lambda: recovered(deadline)))
                    return
                for cover in covers:
                    assert cover._request.still_current and cover._request.source_uri == url
                    texture = cover.picture.get_paintable()
                    with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                        assert image.convert("RGB").getpixel((0, 0)) == replacement_pixels
                after_n2 = n2.stats()
                after_n3 = loader.stats()
                assert reads == [doc] and gets == ["GET", "GET"]
                assert after_n2["n1_selected_count"] == before_n2["n1_selected_count"]
                assert after_n2["n2_hit"] >= before_n2["n2_hit"] + 2
                assert after_n3["download_ok"] == before_n3["download_ok"] + 1
                assert after_n3["preview_created"] == before_n3["preview_created"] + 1
                assert after_n3["ui_applied"] >= before_n3["ui_applied"] + 2
                assert after_n3["ui_failed"] == before_n3["ui_failed"]
                assert source.read_bytes() == replacement_body
                assert window.service._account_context_epoch == account_epoch
                assert window.service._source_context_epoch == source_epoch
                capture("00-notice-c-warm-n2-cold-n3-recovered")
                report["checks"]["P0_4980_warm_n2_cold_n3_real_gtk"] = True
                report["checks"]["P0_6366_mainwindow_library_recent_restart_offline"] = True
                report["checks"]["P0_4980_same_c_six_physical_views_restart_offline"] = True
                # The same URL bytes may serve two distinct C documents.
                # Rebind only the list surface, keeping the original detail
                # mapped, and require a new documentary read but no N3 GET.
                verify_independent_c(matrix, list_cover, detail_cover)

            later(40, guard(lambda: recovered(monotonic() + 8)))

        def verify_independent_c(matrix, list_cover, detail_cover):
            report["local_c_stage"] = "distinct_c_shared_n3"
            before_n2 = n2.stats()
            before_n3 = loader.stats()
            old_request = list_cover._request
            detail_request = detail_cover._request
            list_cover.bind_document(n2, other_doc, variant="list",
                                     minimum_revision=other_epoch)

            def observed(deadline):
                if (list_cover._request is old_request
                        or not list_cover.get_mapped()
                        or not isinstance(list_cover.picture.get_paintable(), Gdk.Texture)
                        or list_cover.placeholder.get_visible()
                        or list_cover.spinner.get_visible()
                        or reads != [doc, other_doc]):
                    assert monotonic() < deadline, "Independent C / shared N3 GTK timeout"
                    later(40, guard(lambda: observed(deadline)))
                    return
                assert not old_request.active
                assert list_cover._request.still_current
                assert list_cover._request.source_uri == url
                assert detail_cover._request is detail_request and detail_request.still_current
                assert detail_cover.get_mapped()
                for cover in (list_cover, detail_cover):
                    texture = cover.picture.get_paintable()
                    assert isinstance(texture, Gdk.Texture)
                    with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                        assert image.convert("RGB").getpixel((0, 0)) == replacement_pixels
                after_n2, after_n3 = n2.stats(), loader.stats()
                assert gets == ["GET", "GET"]
                assert after_n2["n2_register_new"] == before_n2["n2_register_new"] + 1
                assert after_n2["n1_selected_count"] == before_n2["n1_selected_count"] + 1
                assert after_n3["download_ok"] == before_n3["download_ok"]
                assert after_n3["preview_created"] == before_n3["preview_created"]
                assert after_n3["ui_applied"] >= before_n3["ui_applied"] + 1
                assert after_n3["ui_failed"] == before_n3["ui_failed"]
                assert window.service._account_context_epoch == account_epoch
                assert window.service._source_context_epoch == source_epoch
                capture("00-notice-c-distinct-c-shared-n3")
                report["checks"]["P0_4980_distinct_c_shared_n3_real_gtk"] = True
                verify_invalidated_n2_warm_n3(list_cover, detail_cover)

            later(40, guard(lambda: observed(monotonic() + 8)))

        def verify_invalidated_n2_warm_n3(list_cover, detail_cover):
            report["local_c_stage"] = "invalidated_n2_warm_n3"
            previous = detail_cover._request
            other = list_cover._request
            before_n2, before_n3 = n2.stats(), loader.stats()

            n2.decisions.invalidate(doc, cause="evidence_changed")
            assert not previous.still_current
            assert other.still_current
            detail_cover.bind_document(n2, doc, variant="detail", minimum_revision=epoch)

            def refreshed(deadline):
                texture = detail_cover.picture.get_paintable()
                if (not detail_cover.get_mapped()
                        or not isinstance(texture, Gdk.Texture)
                        or detail_cover.placeholder.get_visible()
                        or detail_cover.spinner.get_visible()):
                    assert monotonic() < deadline, "Invalidated N2 / warm N3 GTK timeout"
                    later(40, guard(lambda: refreshed(deadline)))
                    return
                current = detail_cover._request
                assert current is not previous and current.still_current
                assert not previous.active and current.source_uri == url
                assert list_cover._request is other and other.still_current
                assert list_cover.get_mapped()
                with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                    assert image.convert("RGB").getpixel((0, 0)) == replacement_pixels
                after_n2, after_n3 = n2.stats(), loader.stats()
                assert reads == [doc, other_doc] and gets == ["GET", "GET"]
                assert after_n2["n1_selected_count"] == before_n2["n1_selected_count"] + 1
                assert after_n2["n2_resolution_miss"] == before_n2["n2_resolution_miss"] + 1
                assert after_n3["cache_hit_valid"] > before_n3["cache_hit_valid"]
                assert after_n3["download_ok"] == before_n3["download_ok"]
                assert after_n3["preview_created"] == before_n3["preview_created"]
                assert after_n3["ui_failed"] == before_n3["ui_failed"]
                capture("00-notice-c-invalidated-n2-warm-n3")
                report["checks"]["P0_4980_invalidated_n2_warm_n3_real_gtk"] = True
                verify_epub_priority(list_cover, detail_cover)

            later(40, guard(lambda: refreshed(monotonic() + 8)))

        def verify_epub_priority(list_cover, detail_cover):
            # Independent, explicitly synthetic Artifact/Placement proof.
            # Neither this local fixture nor the already warm catalogue URL
            # establishes a new relation to the three-source C above.
            from cifereca import epub_metadata
            from cifereca.core.book_cover_projection import CoverDocumentScope
            from cifereca.core.covers import candidate
            from cifereca.core.epub_cover_source import project_exact_embedded_cover
            from cifereca.gui.document_cover_service import CoverSourceSnapshot
            from zipfile import ZipFile

            report["local_c_stage"] = "epub_priority_cold"
            cache_root = root / "embedded-fixture-cache"

            def isolated_cache(*, create=True):
                if create:
                    cache_root.mkdir(parents=True, exist_ok=True)
                return cache_root

            # All imported cover_cache_dir functions use this exact module
            # global. The temporary override avoids touching any other cache.
            cache_patch = patch.object(epub_metadata, "cache_dir", isolated_cache)
            cache_patch.start()
            state["epub_cache_patch"] = cache_patch
            embedded_pixels = (39, 154, 74)

            def prepare():
                assert get_ident() != gtk_thread
                epub = root / "priority-fixture.epub"
                png = BytesIO()
                Image.new("RGB", (400, 600), embedded_pixels).save(png, format="PNG")
                png_bytes = png.getvalue()
                with ZipFile(epub, "w") as archive:
                    archive.writestr("mimetype", "application/epub+zip")
                    archive.writestr("META-INF/container.xml",
                        "<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
                        "<rootfiles><rootfile full-path='OPS/content.opf'/></rootfiles></container>")
                    archive.writestr("OPS/content.opf",
                        "<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>"
                        "<metadata><dc:title xmlns:dc='http://purl.org/dc/elements/1.1/'>"
                        "EPUB fixture</dc:title></metadata><manifest>"
                        "<item id='cover' href='cover.png' media-type='image/png' "
                        "properties='cover-image'/></manifest><spine/></package>")
                    archive.writestr("OPS/cover.png", png_bytes)
                digest = hashlib.sha256(epub.read_bytes()).hexdigest()
                metadata = epub_metadata.inspect_epub(epub)
                scope = CoverDocumentScope(
                    kind="placement", identity_id=owner,
                    source_key="artifact:sha256:" + digest,
                    artifact_id="sha256:" + digest,
                    placement_id="fixture:epub-priority",
                )
                embedded = project_exact_embedded_cover(
                    scope, revision=1, artifact_sha256=digest, artifact_metadata=metadata,
                )
                assert embedded is not None
                physical = epub_metadata.cover_cache_dir() / embedded.resource.cache_name
                assert physical.is_relative_to(cache_root)
                assert physical.read_bytes() == png_bytes
                physical.unlink()
                assert not physical.exists()

                # Kill a separate publisher before atomic rename. Its cache
                # override is confined to this synthetic fixture, not the app.
                import subprocess
                script = """
import os
import sys
from pathlib import Path
from cifereca import epub_metadata

root, image_path, epub_path = map(Path, sys.argv[1:])
epub_metadata.cache_dir = lambda **kwargs: root
from zipfile import ZipFile
with ZipFile(epub_path) as archive:
    data = archive.read("OPS/cover.png")

def interrupted(source, target):
    assert Path(target) == image_path
    assert Path(source).read_bytes() == data
    assert not image_path.exists()
    os._exit(73)

epub_metadata.os.replace = interrupted
epub_metadata._publish_cover(data, "png")
"""
                child = subprocess.run(
                    [sys.executable, "-c", script, str(cache_root),
                     str(physical), str(epub)],
                    capture_output=True, timeout=20,
                )
                assert child.returncode == 73, child.stderr.decode(errors="replace")
                assert not physical.exists()
                leftovers = list(physical.parent.glob(".cover-*"))
                assert len(leftovers) == 1
                assert not epub_metadata.valid_cached_cover_path(leftovers[0])
                catalogue = candidate(url, tier="catalog", source="fixture",
                                      evidence_id="fixture:epub-priority")
                assert catalogue is not None
                snapshot = CoverSourceSnapshot(
                    scope=scope, revision=1, candidates=(catalogue,),
                    embedded=embedded, verified_epub_path=epub,
                )
                return snapshot, physical, png_bytes

            def show_embedded(prepared):
                snapshot, physical, png_bytes = prepared
                embedded = snapshot.embedded
                assert embedded is not None
                assert loader._path(url).read_bytes() == replacement_body
                scope = snapshot.scope
                before_n2, before_n3 = n2.stats(), loader.stats()
                n2.register(snapshot)

                def bind():
                    for variant, cover in (("list", list_cover), ("detail", detail_cover)):
                        cover.bind_document(n2, scope, variant=variant)

                def wait_embedded(deadline, warm, base_n2, base_n3):
                    covers = (list_cover, detail_cover)
                    if not all(
                        cover.get_mapped()
                        and isinstance(cover.picture.get_paintable(), Gdk.Texture)
                        and not cover.placeholder.get_visible()
                        and not cover.spinner.get_visible()
                        for cover in covers
                    ):
                        assert monotonic() < deadline, "EPUB priority GTK timeout"
                        later(40, guard(lambda: wait_embedded(deadline, warm, base_n2, base_n3)))
                        return
                    for cover in covers:
                        request = cover._request
                        assert request.still_current and request.source_uri == embedded.candidate.url
                        texture = cover.picture.get_paintable()
                        with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                            assert image.convert("RGB").getpixel((0, 0)) == embedded_pixels
                    decision = n2.decisions.peek(scope, revision=1)
                    assert decision is not None and decision.tier == "embedded"
                    assert physical.read_bytes() == png_bytes
                    assert loader._path(url).read_bytes() == replacement_body
                    assert reads == [doc, other_doc] and gets == ["GET", "GET"]
                    current_n2, current_n3 = n2.stats(), loader.stats()
                    assert current_n2["n1_selected_count"] == base_n2["n1_selected_count"] + (0 if warm else 1)
                    assert current_n3["n3_epub_miss"] == base_n3["n3_epub_miss"] + (0 if warm else 1)
                    assert current_n3["n3_epub_ok_recent_count"] == base_n3["n3_epub_ok_recent_count"] + (0 if warm else 1)
                    assert current_n3["n3_epub_failed"] == base_n3["n3_epub_failed"]
                    assert current_n3["download_ok"] == base_n3["download_ok"]
                    assert current_n3["ui_applied"] >= base_n3["ui_applied"] + 2
                    assert current_n3["ui_failed"] == base_n3["ui_failed"]
                    assert window.service._account_context_epoch == account_epoch
                    assert window.service._source_context_epoch == source_epoch
                    if not warm:
                        capture("00-notice-c-epub-priority-cold")
                        report["checks"]["P0_4980_epub_priority_cold_real_gtk"] = True
                        report["checks"]["P0_4980_epub_interrupted_publish_recovery_real_gtk"] = True
                        report["local_c_stage"] = "epub_priority_warm"
                        bind()
                        later(40, guard(lambda: wait_embedded(
                            monotonic() + 8, True, current_n2, current_n3)))
                        return
                    assert current_n2["n2_hit"] >= base_n2["n2_hit"] + 2
                    assert current_n3["n3_epub_hit"] >= base_n3["n3_epub_hit"] + 2
                    capture("00-notice-c-epub-priority-warm")
                    report["checks"]["P0_4980_epub_priority_warm_real_gtk"] = True
                    verify_qualified_fallback(snapshot, physical)

                bind()
                later(40, guard(lambda: wait_embedded(
                    monotonic() + 8, False, before_n2, before_n3)))

            def verify_qualified_fallback(snapshot, physical):
                # The original EPUB is unchanged and has a verified SHA. A
                # stale N2 member coordinate deliberately names a non-entry:
                # only the existing bounded worker may qualify this as a
                # *definitive* failure and switch N2 via its exact CAS.
                report["local_c_stage"] = "epub_qualified_fallback"
                assert snapshot.embedded is not None and physical.is_file()
                physical.unlink()
                embedded = replace(
                    snapshot.embedded, resource=replace(
                        snapshot.embedded.resource,
                        member="OPS/fixture-cover-missing.png", source_revision=2,
                    ),
                )
                broken = replace(snapshot, revision=2, embedded=embedded)
                before_n2, before_n3 = n2.stats(), loader.stats()
                before_reads, before_gets = tuple(reads), tuple(gets)
                assert loader._path(url).read_bytes() == replacement_body
                n2.register(broken)
                for variant, cover in (("list", list_cover), ("detail", detail_cover)):
                    cover.bind_document(n2, broken.scope, variant=variant)

                def fallback_painted(deadline):
                    decision = n2.decisions.peek(broken.scope, revision=2)
                    covers = (list_cover, detail_cover)
                    ready = (
                        decision is not None and decision.status == "selected"
                        and decision.tier == "catalog" and decision.uri == url
                        and all(
                            cover.get_mapped() and cover._request is not None
                            and cover._request.still_current
                            and cover._request.source_uri == url
                            and isinstance(cover.picture.get_paintable(), Gdk.Texture)
                            and not cover.placeholder.get_visible()
                            and not cover.spinner.get_visible()
                            for cover in covers
                        )
                    )
                    if not ready:
                        assert monotonic() < deadline, "qualified EPUB fallback GTK timeout"
                        later(40, guard(lambda: fallback_painted(deadline)))
                        return
                    for cover in covers:
                        texture = cover.picture.get_paintable()
                        with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                            assert image.convert("RGB").getpixel((0, 0)) == replacement_pixels
                    after_n2, after_n3 = n2.stats(), loader.stats()
                    assert not physical.exists()  # No false EPUB restoration.
                    assert tuple(reads) == before_reads and tuple(gets) == before_gets
                    assert after_n2["n1_selected_count"] >= before_n2["n1_selected_count"] + 2
                    assert after_n3["n3_epub_miss"] >= before_n3["n3_epub_miss"] + 1
                    assert after_n3["n3_epub_failed"] >= before_n3["n3_epub_failed"] + 1
                    assert after_n3["download_ok"] == before_n3["download_ok"]
                    assert after_n3["ui_applied"] >= before_n3["ui_applied"] + 2
                    assert window.service._account_context_epoch == account_epoch
                    assert window.service._source_context_epoch == source_epoch
                    capture("00-notice-c-epub-qualified-fallback")
                    report["checks"]["P0_4980_epub_qualified_fallback_real_gtk"] = True
                    verify_catalogue_http_fallback(list_cover, detail_cover)

                later(40, guard(lambda: fallback_painted(monotonic() + 12)))

            pending = pool.submit(prepare)
            await_job(pending, show_embedded, monotonic() + 8)

        def verify_catalogue_http_fallback(list_cover, detail_cover):
            from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
            from cifereca.core.notice_cover_repository import persist_notice_cover
            from cifereca.core.notice_source_observation import NoticeSourceKey, admit_source_notice

            report["local_c_stage"] = "catalogue_http_fallback"
            original_gets = tuple(gets)

            def prepare():
                for index, address in enumerate(fallback_images, 1):
                    provider = f"fixture-fallback-{index}"
                    notice = admit_source_notice(
                        key=NoticeSourceKey(
                            provider_id=provider, visibility="public", identity_id=None,
                            credential_id=None, account_id=None, branch_id="catalogue",
                            record_id=provider, edition_id=provider, manifestation_role="digital",
                        ),
                        parser_id="fixture", parser_version="1", observed_at=1,
                        status="complete", raw_fields={"cover": address},
                    )
                    source = persist_notice_cover(
                        db, notice, field_path=("cover",), cover_inventory_complete=True,
                    )
                    aggregate = persist_consolidated_cover_source(
                        db, canonical_edition_id="fixture:catalogue-http-fallback", source=source,
                        relation_proof_id=f"fixture:catalogue-http-fallback:{index}",
                    )
                assert len(aggregate.sources) == 3
                return aggregate

            def show_fallback(aggregate):
                state["catalogue_fallback"] = True
                covers = (list_cover, detail_cover)
                for variant, cover in zip(("list", "detail"), covers):
                    cover.bind_document(n2, aggregate.document, variant=variant,
                                        minimum_revision=aggregate.epoch)

                def painted(deadline):
                    if not all(
                        cover.get_mapped() and cover._request is not None
                        and cover._request.still_current
                        and cover._request.source_uri == fallback_images[-1]
                        and isinstance(cover.picture.get_paintable(), Gdk.Texture)
                        and not cover.placeholder.get_visible() and not cover.spinner.get_visible()
                        for cover in covers
                    ):
                        assert monotonic() < deadline, "qualified catalogue HTTP fallback GTK timeout"
                        later(40, guard(lambda: painted(deadline)))
                        return
                    for cover in covers:
                        texture = cover.picture.get_paintable()
                        with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                            assert image.convert("RGB").getpixel((0, 0)) == pixels
                    assert tuple(fallback_gets) == fallback_images and tuple(gets) == original_gets
                    assert window.service._account_context_epoch == account_epoch
                    assert window.service._source_context_epoch == source_epoch
                    capture("00-notice-c-catalogue-http-fallback")
                    report["checks"]["P0_4980_catalogue_http_fallback_real_gtk"] = True
                    verify_postcommit_rebind(list_cover, detail_cover)

                later(40, guard(lambda: painted(monotonic() + 8)))

            await_job(pool.submit(prepare), show_fallback, monotonic() + 8)

        def verify_postcommit_rebind(list_cover, detail_cover):
            """A new exact C proof repaints two subscribed real widgets."""
            from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
            from cifereca.core.notice_cover_repository import persist_notice_cover
            from cifereca.core.notice_source_observation import NoticeSourceKey, admit_source_notice

            report["local_c_stage"] = "postcommit_rebind"
            covers = (list_cover, detail_cover)
            original_gets = tuple(gets)
            original_fallback_gets = tuple(fallback_gets)
            initial_reads = len(reads)

            def publish(address, stamp, expected):
                assert get_ident() != gtk_thread
                provider = "fixture-postcommit"
                notice = admit_source_notice(
                    key=NoticeSourceKey(
                        provider_id=provider, visibility="public", identity_id=None,
                        credential_id=None, account_id=None, branch_id="catalogue",
                        record_id=provider, edition_id=provider, manifestation_role="digital",
                    ),
                    parser_id="fixture", parser_version="1", observed_at=stamp,
                    status="complete", raw_fields={"cover": address},
                )
                source = persist_notice_cover(
                    db, notice, field_path=("cover",), cover_inventory_complete=True,
                )
                return persist_consolidated_cover_source(
                    db, canonical_edition_id="fixture:postcommit", source=source,
                    relation_proof_id="fixture:postcommit:exact", expected_epoch=expected,
                )

            def ready(address):
                return all(
                    cover.get_mapped() and cover._request is not None
                    and cover._request.still_current and cover._request.source_uri == address
                    and isinstance(cover.picture.get_paintable(), Gdk.Texture)
                    and not cover.placeholder.get_visible() and not cover.spinner.get_visible()
                    for cover in covers
                )

            def assert_pixels(expected):
                for cover in covers:
                    texture = cover.picture.get_paintable()
                    with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                        assert image.convert("RGB").getpixel((0, 0)) == expected

            def show_initial(initial):
                for variant, cover in zip(("list", "detail"), covers):
                    cover.bind_document(n2, initial.document, variant=variant,
                                        minimum_revision=initial.epoch)

                def initial_painted(deadline):
                    if not ready(url):
                        assert monotonic() < deadline, "postcommit initial GTK timeout"
                        later(40, guard(lambda: initial_painted(deadline)))
                        return
                    assert_pixels(replacement_pixels)
                    assert reads[initial_reads:] == [initial.document]
                    old_requests = tuple(cover._request for cover in covers)

                    def commit():
                        updated = publish(fallback_images[-1], 2, initial.epoch)
                        assert updated.document == initial.document
                        assert updated.epoch > initial.epoch
                        # Actual N2 observer, after the authoritative SQLite commit.
                        assert n2.refresh_committed_source_in_worker(updated.document, updated.epoch)
                        return updated

                    def committed(updated):
                        def repainted(deadline):
                            if not ready(fallback_images[-1]):
                                assert monotonic() < deadline, "postcommit subscribed GTK timeout"
                                later(40, guard(lambda: repainted(deadline)))
                                return
                            # No bind, click, selection or view refresh since commit.
                            assert all(cover._request is not old and not old.still_current
                                       for cover, old in zip(covers, old_requests))
                            assert_pixels(pixels)
                            assert reads[initial_reads:] == [updated.document, updated.document]
                            assert tuple(gets) == original_gets
                            assert tuple(fallback_gets) == original_fallback_gets
                            assert window.service._account_context_epoch == account_epoch
                            assert window.service._source_context_epoch == source_epoch
                            report["checks"]["P0_6168_postcommit_rebind_real_gtk"] = True
                            def finish_matrix():
                                report["local_c_stage"] = "complete"
                                cleanup()
                                window.present()
                                complete()

                            from notice_c_preview_repair_contract import verify_preview_repair
                            report["local_c_stage"] = "preview_decode_repair"
                            verify_preview_repair(
                                list_cover, detail_cover, n2=n2, loader=loader,
                                document=updated.document, revision=updated.epoch,
                                url=fallback_images[-1], pixels=pixels,
                                counters=lambda: (tuple(reads), tuple(gets), tuple(fallback_gets)),
                                later=later, guard=guard, capture=capture,
                                report=report, complete=finish_matrix,
                            )

                        later(40, guard(lambda: repainted(monotonic() + 8)))

                    await_job(pool.submit(commit), committed, monotonic() + 8)

                later(40, guard(lambda: initial_painted(monotonic() + 8)))

            await_job(pool.submit(publish, url, 1, 0), show_initial, monotonic() + 8)

        stamp_value = 1794900000
        future = pool.submit(project)
        await_job(future, show, monotonic() + 10)

    def seeded(facts):
        state["facts"] = facts
        start_stage(False)

    later(40, guard(lambda: await_job(job, seeded, monotonic() + 10)))
