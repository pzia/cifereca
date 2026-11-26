"""Search-detail view adapter; the host retains identity and task generations.

Every asynchronous result uses the existing captured context. Selection,
cache cancellation, task runner, UI-thread delivery and service ownership stay
on MainWindow. No import of MainWindow or new subscription belongs here.
"""
from __future__ import annotations

from .search_host_port import SearchDetailHost

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..models import SearchResult, ItemDetail
from .book_widgets import BookListItem
from .query_model import result_row
from .detail_model import book_detail_presentation, detail_language
from .local_book_detail_sections import local_book_detail_sections
from .task_runner import TaskPriority
from .search_detail_context import detail_callback_is_current


def _search_selected(self: SearchDetailHost, row: BookListItem | None) -> None:
    if getattr(self, "_closed", False):
        return
    # A result id alone is not enough: ids can be reused by another
    # provider, selection, query, or identity while holdings are in flight.
    self._search_detail_request_generation += 1
    self._cancel_cached_book_details()
    if row is None:
        self._selected_search_id = None
        self.search_page.detail.empty("Aucun document sélectionné", "Sélectionnez un résultat pour afficher ses détails.")
        return
    if row.key in self._search_local_objects:
        item = self._search_local_objects.get(row.key)
        if not item:
            return
        self._selected_search_id = row.key
        if item.get("source") == "notice_c_public":
            # Search C is a descriptive, potentially stale source observation.
            # It is NEVER an entitlement, a v2 known-available action or a
            # session/offer. The detail receives the same exact document as
            # the standard list, without a new provider request or an
            # arbitrary image URL fallback. No plugin/cached actions.
            from dataclasses import replace
            from cifereca.core.book_cover_projection import CoverDocumentScope
            from cifereca.core.notice_cover_repository import is_notice_cover_document
            document = item.get("_cover_document_ref")
            active = getattr(
                getattr(self.service, "current_identity", None),
                "identity_id", None,
            )
            panel = self.search_page.detail
            if (type(document) is not CoverDocumentScope
                    or document.kind != "public_catalogue"
                    or document.identity_id is not None
                    or not is_notice_cover_document(document)
                    or item.get("identity_id") != active):
                panel.empty("Notice indisponible", "Source documentaire périmée.")
                return
            metadata = item.get("metadata")
            metadata = metadata if type(metadata) is dict else {}
            stale = metadata.get("notice_c_stale") is True
            # Same bounded source facts as the C cursor used by Recommendations.
            # No dates inferred from cache freshness, loan or acquisition.
            from cifereca.core.public_search_projection import _public_bibliographic_text

            def public_text(value):
                return value if (
                    type(value) is str and _public_bibliographic_text(value, limit=512)
                    and not value.lstrip().casefold().startswith(("/", "\\", "~/", "file:"))
                ) else None

            from cifereca.gui.search_known_public_c_bibliography import (
                known_public_detail_metadata, known_public_alternative_section,
                with_known_public_field_provenance,
            )
            # The primary C age is a typed Core fact, not a free provider
            # metadata dictionary. Keep the age's own source provenance;
            # never borrow an alternative source's age for this primary row.
            from cifereca.gui.search_known_public_c_age import primary_known_public_c_ages
            detail_metadata = known_public_detail_metadata(metadata)
            primary_ages = primary_known_public_c_ages(item)
            if primary_ages:
                detail_metadata["youth_age_ranges"] = primary_ages
            presentation = book_detail_presentation(
                title=str(item.get("title") or "Sans titre"),
                subtitle=str(item.get("author") or ""),
                thumbnail=None,
                badges=("Notice connue · à actualiser" if stale
                        else "Notice publique connue",),
                section_rows=(("Catalogue", (
                    ("Fournisseur", item.get("provider_id") or "—"),
                    ("Format", public_text(item.get("media_format"))),
                    ("Éditeur", public_text(metadata.get("publisher"))),
                    ("Publication", public_text(item.get("published_at"))),
                    ("Disponibilité", "Non vérifiée"),
                    ("Couverture de recherche", "Partielle"),
                )),),
                metadata=detail_metadata,
            )
            from cifereca.gui.search_known_public_c_guard import (
                known_public_c_cell_current,
            )
            from cifereca.gui.local_public_c_detail_guard import (
                staged_local_public_c_detail,
            )
            # Consume the receipt already attached at list publication;
            # a new selection cannot mint freshness for an old GTK row.
            binding = getattr(row, "_cover_known_public_c_receipt", None)
            detail_generation = self._search_detail_request_generation

            def current_c_detail() -> bool:
                if (self._search_detail_request_generation != detail_generation
                        or not known_public_c_cell_current(self, row, binding)):
                    return False
                # Selection can change while N3 is materialising. A shared
                # catalogue ref never transfers the old detail selection.
                selection = getattr(self.search_page, "selection", None)
                get_selected = getattr(selection, "get_selected_item", None)
                return not callable(get_selected) or get_selected() is row

            if binding is None or not current_c_detail():
                panel.empty("Notice indisponible", "Source documentaire périmée.")
                return
            presentation = with_known_public_field_provenance(presentation, item)
            alternatives = known_public_alternative_section(metadata, item=item)
            if alternatives is not None:
                presentation = replace(
                    presentation, sections=(*presentation.sections, alternatives),
                )
            with staged_local_public_c_detail(
                panel, current_c_detail, minimum_revision=binding.epoch,
            ):
                panel.show_presentation(
                    replace(presentation, cover_document_ref=document),
                    description=metadata.get("description"),
                )
            from cifereca.gui.notice_c_web_action_controller import request_public_web_actions
            request_public_web_actions(
                self, panel, document, expected_epoch=binding.epoch,
                current=current_c_detail,
            )
            from cifereca.gui.cross_profile_possible_lender_detail import append_c_possible_lender_summary
            append_c_possible_lender_summary(
                panel, document, expected_epoch=binding.epoch, current=current_c_detail,
            )
            return
        description = self._book_description(item)
        # The same *known v2 exact notice* may already have a validated
        # holdings image in the shared N2 decision. Only presentation uses
        # it; neither the Library row nor its canonical rights are modified.
        visual_thumbnail = item.get("thumbnail_url")
        if item.get("source") == "known_catalog":
            from cifereca.gui.search_detail_cover_reuse import (
                cached_exact_selected_search_cover,
            )
            qualified = self._known_search_result(item)
            if qualified is not None:
                visual_thumbnail = (
                    cached_exact_selected_search_cover(self, qualified)
                    or visual_thumbnail
                )
        # Search-local is a second view of a previously loaded Core book.
        # Only its exact owner-scoped Artifact+Placement proof may use the
        # shared DocumentRef; a hot cached URL/ISBN/title cannot mint one.
        # Use an absolute import because the headless Search harness replays
        # this function's AST outside its normal module globals.
        from cifereca.gui.local_detail_cover import exact_local_detail_cover
        active = getattr(
            getattr(self.service, "current_identity", None), "identity_id", None,
        )
        cover_evidence = exact_local_detail_cover(
            item, active_identity_id=active,
        )
        if (item.get("_exact_local_cover_evidence") is not None
                and cover_evidence is None):
            # A stale, foreign or malformed Core proof cannot authorize a
            # provider URL fallback under the old book's visual identity.
            visual_thumbnail = None
        presentation = book_detail_presentation(
            title=str(item.get("title") or "Sans titre"),
            subtitle=str(item.get("author") or ""),
            thumbnail=visual_thumbnail,
            badges=[],
            section_rows=local_book_detail_sections(
                item,
                provider_display_name=self.service.provider_display_name,
                availability_label=self._library_availability_label,
                identity_by_id=self.service.identity_by_id,
                sync_status_label=self._kobo_backup_status_label,
                language_label=detail_language,
                offline_if_local=True,
            ),
            metadata=item.get('metadata'),
        )
        detail = self.search_page.detail
        if cover_evidence is None:
            detail.show_presentation(presentation, description=description)
        else:
            detail.show_local_presentation(
                presentation, description=description,
                cover_evidence=cover_evidence,
            )
        actions = []
        open_library = Gtk.Button(label="Voir dans Bibliothèque")
        open_library.connect("clicked", lambda _b, key=row.key: self._open_library_item(key))
        if item.get("local") or item.get("state") in {"remote_owned", "remote_loan_active", "remote_loan_realized"}:
            actions.append(open_library)
        if item.get("source") == "known_catalog":
            candidate = self._known_search_result(item)
            if candidate:
                # A cached button may outlive a selection, new search or
                # identity A→B→A. Check the captured Search generation BEFORE
                # requesting holdings, not only when the response returns.
                context = self._search_detail_request_context()
                expected_key = row.key
                show = Gtk.Button(label="Disponibilités / téléchargement")
                show.connect(
                    "clicked",
                    lambda _b, notice=candidate, key=expected_key, ctx=context: (
                        self._load_known_detail(notice)
                        if self._search_detail_context_current(key, ctx) else None
                    ),
                )
                actions.append(show)
        extend = Gtk.Button(label="En ligne")
        extend.add_css_class("suggested-action")
        extend.connect("clicked", lambda _b: self.run_search(True)); actions.append(extend)
        self.search_page.detail.add_actions(actions)
        self._add_local_book_actions(self.search_page.detail,item)
        self._add_facets(self.search_page.detail, item, self.search_controls)
        # Local dictionary presentations do not run DetailPanel._decorate.
        # Remote SearchResult presentations already own that decoration.
        self._add_plugin_book_actions(self.search_page.detail, item)
        self._request_cached_book_details("search", row.key, item)
        return
    item = self.search_objects.get(row.key)
    if not item: return
    self._selected_search_id = item.id
    if (getattr(item, "metadata", None) or {}).get("search_provenance") == "known_catalog_stale":
        # The stale catalogue is read-only evidence. Merely selecting it must
        # not start a holdings/authentication request to a provider in outage.
        self.search_page.detail.show_search(item, loading=False)
        self._add_facets(self.search_page.detail, result_row(item), self.search_controls)
        from .notice_c_private_series_controller import request_selected_retained_series
        request_selected_retained_series(
            self, item, selected_id=item.id, context=self._search_detail_request_context(),
        )
        # A stale public row must remain purely descriptive. Do not even
        # capture a private selection lease for an anonymous row.
        if (getattr(item, "source_credential", None)
                and item.source_credential != "__public__"
                and getattr(item, "source_account_id", None)):
            from cifereca.gui.notice_c_web_action_controller import request_selected_authenticated_web_actions
            request_selected_authenticated_web_actions(
                self, item, selected_id=item.id, context=self._search_detail_request_context(),
            )
        return
    from cifereca.gui.search_detail_cover_reuse import visual_search_item
    # Re-selection must not drop a previously attested exact-source image
    # while a new holdings lookup runs. Keep the original SearchResult for
    # all authorizations and provider actions.
    from cifereca.gui.search_document_cover_bridge import exact_remote_search_document
    renderer = getattr(self.search_page.detail, "show_search_document", None)
    proof = exact_remote_search_document(self, item, row.key)
    if proof is not None and callable(renderer):
        renderer(item, loading=True, cover_evidence=proof)
    else:
        self.search_page.detail.show_search(visual_search_item(self, item), loading=True)
    self._add_facets(self.search_page.detail, result_row(item), self.search_controls)
    self._add_local_book_actions(self.search_page.detail,result_row(item))
    self._request_cached_book_details("search", item.id, result_row(item))
    selected_id = item.id
    context = self._search_detail_request_context()
    from cifereca.gui.notice_c_web_action_controller import request_selected_authenticated_web_actions
    request_selected_authenticated_web_actions(self, item, selected_id=selected_id, context=context)
    from .notice_c_private_series_controller import request_selected_retained_series
    request_selected_retained_series(self, item, selected_id=selected_id, context=context)
    from .notice_parse_witness_controller import request_selected_parse_witness
    request_selected_parse_witness(self, item, selected_id=selected_id, context=context)
    # The four-field selection lease predates Core's independent account
    # and source epochs. A disconnect/rebind can replace the credential
    # session *without changing the selected book ID or GTK generation*.
    # Capture Core authority at admission; the account-navigation epoch
    # gates only publication. Actual source revocation still stops work.
    holdings_core = self.service
    holdings_query = self._last_search_query
    from cifereca.core.search_orchestration import capture_search_read_scope
    try:
        request_scope = capture_search_read_scope(holdings_core, context[3])
    except (AttributeError, ValueError):
        self._search_detail_error(self._selected_search_id, item,
                                  ValueError("holdings_identity_unavailable"), context=context)
        return
    source_epochs = (
        getattr(self.service, "_account_context_epoch", None),
        getattr(self.service, "_source_context_epoch", None),
    )

    def epochs_current() -> bool:
        return (not getattr(self, "_closed", False)
                and self.service is holdings_core
                and request_scope.current(holdings_core)
                and source_epochs == (
            getattr(self.service, "_account_context_epoch", None),
            getattr(self.service, "_source_context_epoch", None),
        ))

    self.runner.submit(
        "Chargement des disponibilités…",
        lambda: holdings_core.holdings_for_scope(request_scope, item, holdings_query, force=False)
        if (not getattr(self, "_closed", False) and self.service is holdings_core
            and request_scope.current(holdings_core)) else None,
        lambda detail: self._apply_search_detail(selected_id, item, detail, context=context)
        if epochs_current() else None,
        lambda exc: self._search_detail_error(selected_id, item, exc, context=context)
        if epochs_current() else None,
        priority=TaskPriority.METADATA, kind="holdings",
        # Selection-triggered metadata is passive; callback failures are diagnostic.
        origin="automatic",
    )


def _search_detail_request_context(self: SearchDetailHost) -> tuple[int, int, int, str | None]:
    identity = self.service.current_identity
    return (
        self._search_detail_request_generation,
        self._identity_context_generation,
        getattr(self, "_search_generation", 0),
        identity.identity_id if identity is not None else None,
    )


def _search_detail_context_current(
    self: SearchDetailHost, selected_id: str, context: tuple[int, int, int, str | None] | None,
) -> bool:
    if self._closed or self._selected_search_id != selected_id:
        return False
    return detail_callback_is_current(
        closed=False, selected_id=selected_id, expected_id=selected_id,
        captured=context,
        current=self._search_detail_request_context() if context is not None else None,
    )


def _publish_search_detail_cover(
    self: SearchDetailHost, item: SearchResult, detail: ItemDetail,
    *, row: BookListItem | None = None,
) -> None:
    """Apply a *same-notice* cover to the visible Search row, without reselect.

    The holdings callback already passed generation/identity guards. Require
    the current selected GTK key to resolve to the exact source object and a
    matching provider, credential, edition and docbase; homonymous results
    and duplicate supplier IDs are not image provenance.
    """
    mode = getattr(self, "_search_mode", None)
    if (type(item) is not SearchResult or type(detail) is not ItemDetail
            or mode not in ("global", "local")
            or detail.source_provider != item.source_provider
            or detail.source_credential != item.source_credential
            or detail.record_id != item.record_id
            or detail.docbase != item.docbase):
        return
    from ..core.covers import candidate
    cover = candidate(detail.thumbnail_url, tier="catalog", source="search-holdings")
    if cover is None:
        return  # Never publish a signed, private, local or malformed URL.
    if row is None:
        page = getattr(self, "search_page", None)
        selection = getattr(page, "selection", None)
        row = selection.get_selected_item() if selection is not None else None
    elif mode != "global":
        return
    if not isinstance(row, BookListItem):
        return
    if mode == "global":
        if getattr(self, "search_objects", {}).get(row.key) is not item:
            return
    else:
        # A known-catalogue Search entry stays in local mode even after the
        # user explicitly opens its provider holdings. Re-admit its *exact*
        # Core v2 notice before publishing the resulting cover to that row.
        # A title, ISBN, legacy catalogue row or foreign credential is NOT
        # sufficient. The query/account generations were already checked in
        # _apply_search_detail before reaching this publisher.
        from ..core.known_catalog_provenance import known_catalog_search_result
        current_identity = getattr(
            getattr(self.service, "current_identity", None), "identity_id", None,
        )
        raw_known = getattr(self, "_search_local_objects", {}).get(row.key)
        qualified = known_catalog_search_result(
            raw_known, identity_id=current_identity,
        )
        if (qualified is None or (
            qualified.id, qualified.source_provider, qualified.source_credential,
            qualified.record_id, qualified.docbase, qualified.medium,
            qualified.source_collection, qualified.source_account_id,
            qualified.revision_id,
        ) != (
            item.id, item.source_provider, item.source_credential,
            item.record_id, item.docbase, item.medium,
            item.source_collection, item.source_account_id,
            item.revision_id,
        )):
            return
    # Exactly scoped to the current row object and Core search/account epochs.
    record = (
        item, cover.url, item.medium,
        getattr(self, "_identity_context_generation", None),
        getattr(self, "_search_generation", None),
        getattr(self.service, "_account_context_epoch", None),
        getattr(self.service, "_source_context_epoch", None),
    )
    prior = getattr(self, "_search_detail_cover_by_key", None)
    covers = dict(prior) if type(prior) is dict and len(prior) < 128 else {}
    covers[row.key] = record
    self._search_detail_cover_by_key = covers
    # N2: the exact same notice can be re-projected into a new GTK row
    # without another holdings request or relying on a mutable DTO.
    identity = getattr(
        getattr(self.service, "current_identity", None), "identity_id", None,
    )
    if type(identity) is str and identity:
        from .search_cover_bridge import SearchCoverBridge
        bridge = getattr(self, "_search_cover_bridge", None)
        if bridge is None:
            bridge = SearchCoverBridge()
            self._search_cover_bridge = bridge
        if isinstance(bridge, SearchCoverBridge):
            bridge.publish(
                item, detail, identity_id=identity,
                context=(
                    getattr(self, "_identity_context_generation", None),
                    getattr(self, "_search_generation", None),
                    getattr(self.service, "_account_context_epoch", None),
                    getattr(self.service, "_source_context_epoch", None),
                ),
            )
    # Attach the new exact decision before notifying a bound GTK cell. An
    # origin promotion can require a rebind even when the URL stays the same.
    from cifereca.gui.search_document_cover_bridge import (
        attach_search_document, exact_remote_search_document,
    )
    proof = exact_remote_search_document(self, item, row.key)
    if proof is not None:
        previous = getattr(row, "_cover_search_evidence", None)
        attach_search_document(row, proof)
        signature = getattr(row, "_public_row_content", None)
        prior_signature = previous.row_signature if previous is not None else None
        if (type(signature) is tuple and signature[0] == "search"
                and signature[-2] == prior_signature):
            row._public_row_content = (*signature[:-2], proof.row_signature, signature[-1])
        if not row.set_thumbnail_url(cover.url) and prior_signature != proof.row_signature:
            row.emit("thumbnail-changed")
    else:
        row.set_thumbnail_url(cover.url)


def _apply_search_detail(
    self: SearchDetailHost, selected_id: str, item: SearchResult, detail: ItemDetail,
    *, context: tuple[int, int, int, str | None] | None = None,
) -> None:
    if not self._search_detail_context_current(selected_id, context):
        return
    from cifereca.gui.search_detail_cover_reuse import visual_search_detail
    # A fresh holdings response may omit a cover even though N2 still owns
    # an admissible, non-expired decision for this precise source and epochs.
    # Use a visual copy only: do NOT publish the old URL as new evidence.
    _publish_search_detail_cover(self, item, detail)
    from cifereca.gui.search_document_cover_bridge import selected_search_document
    proof = selected_search_document(self, item)
    renderer = getattr(self.search_page.detail, "show_search_document", None)
    if proof is not None and callable(renderer):
        renderer(item, cover_evidence=proof, detail=detail)
    else:
        presentation = visual_search_detail(self, item, detail)
        self.search_page.detail.show_item_detail(item, presentation)
    self._add_facets(self.search_page.detail, result_row(item), self.search_controls)
    self._add_local_book_actions(self.search_page.detail, result_row(item))
    self._request_cached_book_details("search", item.id, result_row(item))
    from cifereca.gui.notice_c_web_action_controller import request_selected_authenticated_web_actions
    request_selected_authenticated_web_actions(self, item, selected_id=selected_id, context=context)
    from .notice_c_private_series_controller import request_selected_retained_series
    request_selected_retained_series(self, item, selected_id=selected_id, context=context)
    from .notice_parse_witness_controller import request_selected_parse_witness
    request_selected_parse_witness(self, item, selected_id=selected_id, context=context)
    # Capability-based action; legacy headless hosts without an installed
    # session registry cannot offer it and must not import GTK adapters.
    if getattr(getattr(self, "service", None), "_sessions", None) is not None:
        from .provider_enrichment_action import add_provider_enrichment_action
        add_provider_enrichment_action(
            self, selected_id, item, detail, context,
            button_factory=self.search_page.detail.window.action_button,
        )


def _search_detail_error(
    self: SearchDetailHost, selected_id: str, item: SearchResult, exc: BaseException,
    *, context: tuple[int, int, int, str | None] | None = None,
) -> None:
    if not self._search_detail_context_current(selected_id, context):
        return
    from cifereca.gui.search_detail_cover_reuse import visual_search_item
    from cifereca.gui.search_document_cover_bridge import selected_search_document
    proof = selected_search_document(self, item)
    renderer = getattr(self.search_page.detail, "show_search_document", None)
    if proof is not None and callable(renderer):
        renderer(item, cover_evidence=proof)
    else:
        self.search_page.detail.show_search(visual_search_item(self, item), loading=False)
    self._add_local_book_actions(self.search_page.detail, result_row(item))
    self._request_cached_book_details("search", item.id, result_row(item))
    from cifereca.gui.notice_c_web_action_controller import request_selected_authenticated_web_actions
    request_selected_authenticated_web_actions(self, item, selected_id=selected_id, context=context)
    from .notice_c_private_series_controller import request_selected_retained_series
    request_selected_retained_series(self, item, selected_id=selected_id, context=context)
    from .notice_parse_witness_controller import request_selected_parse_witness
    request_selected_parse_witness(self, item, selected_id=selected_id, context=context)
    # Provider exceptions can embed account IDs, private file paths or signed
    # URLs. Do not echo their arbitrary text into a user-visible GTK toast.
    self.toast("Disponibilités indisponibles. Réessayez ultérieurement.", 5)


def _load_known_detail(self: SearchDetailHost, item):
    if getattr(self, "_closed", False):
        return
    # A cached GTK callback may outlive a switch of identity even if a
    # previous widget reference is retained. Refuse it before provider I/O.
    metadata = getattr(item, "metadata", None)
    if (type(metadata) is dict and metadata.get("catalogue_scope_version") == 2
            and metadata.get("known_catalog_identity_id") !=
            getattr(getattr(self.service, "current_identity", None), "identity_id", None)):
        return
    # Explicitly opening a known-catalogue detail supersedes a previous
    # holdings request, even if the selected list key stays unchanged.
    self._search_detail_request_generation += 1
    selected = self._selected_search_id
    context = self._search_detail_request_context()
    holdings_core = self.service
    holdings_query = self._last_search_query
    from cifereca.core.search_orchestration import capture_search_read_scope
    try:
        request_scope = capture_search_read_scope(holdings_core, context[3])
    except (AttributeError, ValueError):
        self._search_detail_error(self._selected_search_id, item,
                                  ValueError("holdings_identity_unavailable"), context=context)
        return
    source_epochs = (
        getattr(self.service, "_account_context_epoch", None),
        getattr(self.service, "_source_context_epoch", None),
    )

    def epochs_current() -> bool:
        return (not getattr(self, "_closed", False)
                and self.service is holdings_core
                and request_scope.current(holdings_core)
                and source_epochs == (
            getattr(self.service, "_account_context_epoch", None),
            getattr(self.service, "_source_context_epoch", None),
        ))

    self.runner.submit(
        "Disponibilités…",
        lambda: holdings_core.holdings_for_scope(request_scope, item, holdings_query, force=False)
        if (not getattr(self, "_closed", False) and self.service is holdings_core
            and request_scope.current(holdings_core)) else None,
        lambda detail: self._apply_search_detail(selected, item, detail, context=context)
        if epochs_current() else None,
        priority=TaskPriority.USER_ACTION, kind="holdings", origin="user",
    )
