from __future__ import annotations
from typing import Any, TYPE_CHECKING
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
from ..catalogue_link import safe_document_link_url
from .master_detail import MasterDetailPage
from .book_widgets import BookListItem
from .action_widgets import action_button
if TYPE_CHECKING:
    from .main_window import MainWindow


class PublicBookMasterDetailView:
    """Public adapter backed by the same MasterDetailPage used by Core pages."""

    def __init__(
        self, window: "MainWindow", *, title: str, subtitle: str = "",
        empty_title: str = "Aucun document",
        empty_desc: str = "Aucun document à afficher.",
    ) -> None:
        self._page = MasterDetailPage(window, title, subtitle, empty_title, empty_desc)
        self._rows: dict[str, Any] = {}
        self._window = window

    @property
    def widget(self):
        return self._page

    def bind_local_filter_focus(self, callback) -> None:
        """Delegate Ctrl+F to the local controls owned by this plugin page."""
        self._page.bind_local_filter_focus(callback)

    def append_toolbar(self, widget: object) -> None:
        self._page.toolbar.append(widget)

    def _revoke_pending_exact_detail(self) -> None:
        """Cancel an unstarted #4664 holdings lookup when its pane is reset.

        The Core exact-detail worker checks this token before its provider
        call; the late GTK result checks it again. A normal Search detail
        publication does not reset the lease, only a plugin redraw does.
        """
        self._cifereca_exact_detail_request_token = None

    def show_rows(self, rows, total_count: int | None = None) -> None:
        # Materialize exactly once: public plugins may supply a generator.
        # The prior dict/list double iteration consumed it before GTK saw it.
        source_rows = tuple(rows)
        original_keys = [str(row.key) for row in source_rows]
        reserved = set(original_keys)
        mapped = {}
        ui_rows = []
        for index, (row, key) in enumerate(zip(source_rows, original_keys)):
            presented_key = key
            if presented_key in mapped:
                # A source may return two distinct books with the same key.
                # Do not let the final one silently hijack both detail rows.
                # Ephemeral keys are internal to this render and never alter
                # the plugin's original row or its public action context.
                suffix = index + 1
                presented_key = f"{key}::row-{suffix}"
                while presented_key in reserved or presented_key in mapped:
                    suffix += 1
                    presented_key = f"{key}::row-{suffix}"
                reserved.add(presented_key)
            mapped[presented_key] = row
            presentation = (
                str(row.title or "Sans titre"),
                str(row.subtitle or ""),
                str(row.meta or ""),
                str(row.badge or ""),
                getattr(row, "thumbnail_url", None),
            )
            item = BookListItem(presented_key, *presentation)
            # The common GTK cell owns N2 lookup by exact DocumentRef. Only
            # an already attested Core scope may cross this UI boundary:
            # plugin URL, title, ISBN and public row key cannot create one.
            from cifereca.core.book_cover_projection import CoverDocumentScope
            # The Core-attested exact row packet takes precedence over an
            # optional plugin declaration, even if both are syntactically
            # valid but name DIFFERENT documents. No URL/title/ISBN join.
            core_document = getattr(row, "_cover_document_ref", None)
            declared = getattr(row, "cover_document_ref", None)
            if type(core_document) is CoverDocumentScope:
                item._cover_document_ref = core_document
            elif type(declared) is CoverDocumentScope:
                item._cover_document_ref = declared
            elif declared is not None:
                # An invalid explicit reference must not cause a legacy
                # fetch of the plugin's unqualified thumbnail URL.
                item.thumbnail_url = None
            item._public_row_content = presentation
            ui_rows.append(item)
        # Duplicate public plugin keys are occurrence-local, not document
        # identities. On a reordered/replaced snapshot, MasterDetailPage
        # would normally restore the same display key, silently selecting
        # another source and exposing its detail/actions. Preserve selection
        # only while the exact original source object remains under that key.
        from collections import Counter
        collisions = {
            key for key, count in Counter(original_keys).items() if count > 1
        }
        previous_collisions = getattr(self, "_duplicate_source_keys", set())
        previous = getattr(self, "_rows", {})
        selection = getattr(self._page, "selection", None)
        selected = getattr(selection, "get_selected_item", None)
        if callable(selected) and type(previous) is dict:
            active = selected()
            active_key = getattr(active, "key", None)
            former = previous.get(active_key)
            incoming = mapped.get(active_key)
            if (former is not None and incoming is not None
                    and former is not incoming
                    and (str(getattr(former, "key", "")) in
                         (collisions | previous_collisions)
                         or str(getattr(incoming, "key", "")) in
                         (collisions | previous_collisions))):
                # Clear before replacing the mapping so even synchronous
                # GTK selection callbacks cannot dispatch a foreign row.
                selection.set_selected(0xFFFFFFFF)
        self._duplicate_source_keys = collisions
        # Publish mapping before GTK selection notifications during show_rows.
        self._rows = mapped
        # An independently Core-attested canonical C may be exposed through
        # the neutral plugin ABI, but the plugin row is not an evergreen
        # image lease. Even an identical rerender revokes any pending N3
        # callback for the previous snapshot/selection.
        generation = getattr(self, "_public_c_page_generation", 0)
        self._public_c_page_generation = (
            generation + 1 if type(generation) is int and generation >= 0
            else 1
        )
        from cifereca.core.notice_c_cover_index import is_canonical_cover_document
        from cifereca.gui.plugin_public_c_guard import pin_plugin_public_c
        for ui_item in ui_rows:
            document = getattr(ui_item, "_cover_document_ref", None)
            source = mapped.get(ui_item.key)
            if (is_canonical_cover_document(document)
                    or getattr(source, "cover_document_receipt", None) is not None):
                # Keep this veto witness even if the selected identity is
                # absent or the token is refused: no legacy URL fallback.
                ui_item._cover_plugin_c_source = source
                ui_item._cover_plugin_c_receipt = pin_plugin_public_c(
                    self, source, key=ui_item.key,
                )
        self._page.show_rows(ui_rows, total_count=total_count)

    def set_list_state(self, title: str, description: str = "", icon_name: str = "system-search-symbolic") -> None:
        self._revoke_pending_exact_detail()
        self._page.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self._page.empty_page.set_title(str(title))
        self._page.empty_page.set_description(str(description or ""))
        self._page.empty_page.set_icon_name(str(icon_name or "system-search-symbolic"))
        self._page.list_stack.set_visible_child_name("empty")

    def connect_selected(self, callback) -> None:
        def selected(item):
            callback(self._rows.get(str(item.key)) if item is not None else None)
        self._page.connect_selected(selected)

    def show_detail(self, detail) -> None:
        self._revoke_pending_exact_detail()
        panel = self._page.detail
        # Keep exactly ONE neutral detail renderer for every plugin shape.
        # Only canonical C adds an optional current-row N3 lease.
        from contextlib import nullcontext
        stage = nullcontext()
        pinned = None
        from cifereca.core.notice_c_cover_index import is_canonical_cover_document
        document = getattr(detail, "cover_document_ref", None)
        if is_canonical_cover_document(document):
            from cifereca.gui.plugin_public_c_guard import (
                selected_plugin_public_c, plugin_public_c_detail_current,
            )
            pinned = selected_plugin_public_c(self, document)
            if pinned is None:
                # The public edition exists but its association with the
                # current recommendation was revoked. Neither the old
                # DocumentRef nor its raw supplier URL can paint this book.
                from dataclasses import replace
                from cifereca.plugin_api.models import UiBookDetailPresentation
                if type(detail) is UiBookDetailPresentation:
                    detail = replace(
                        detail, cover_document_ref=None,
                        thumbnail_url=None,
                    )
            else:
                selected, receipt = pinned
                from cifereca.gui.local_public_c_detail_guard import (
                    staged_local_public_c_detail,
                )
                stage = staged_local_public_c_detail(
                    panel, lambda: plugin_public_c_detail_current(
                        self, selected, receipt,
                    ),
                    minimum_revision=receipt.minimum_revision,
                )
        with stage:
            panel.show_presentation(detail)
        buttons = []
        window = self._window
        service = getattr(window, "service", None)
        identity = getattr(service, "current_identity", None)
        account_epoch = getattr(service, "_account_context_epoch", None)
        source_epoch = getattr(service, "_source_context_epoch", None)
        route = getattr(window, "_route_display_token", None)
        generation = getattr(self, "_public_c_page_generation", None)

        def action_context_revoked(clicked):
            mapped = getattr(clicked, "get_mapped", None)
            return (getattr(clicked, "_cifereca_action_context_revoked", False)
                    or getattr(clicked, "_cifereca_action_state_blocked", False)
                    or (callable(mapped) and not mapped())
                    or getattr(window, "_closed", False)
                    or self._window is not window
                    or self._page.detail is not panel
                    or getattr(window, "service", None) is not service
                    or getattr(service, "current_identity", None) is not identity
                    or getattr(service, "_account_context_epoch", None) != account_epoch
                    or getattr(service, "_source_context_epoch", None) != source_epoch
                    or getattr(window, "_route_display_token", None) is not route
                    or getattr(self, "_public_c_page_generation", None) != generation)

        for action in getattr(detail, "actions", ()) or ():
            raw_uri = getattr(action, "uri", "")
            if type(raw_uri) is not str:
                continue
            if raw_uri:
                # Plugin actions share the same URL security boundary as
                # document links. Drop invalid URI actions completely.
                uri = safe_document_link_url(raw_uri, relation="related")
                if not uri:
                    continue
                button = Gtk.LinkButton(uri=uri, label=str(action.title))
                button.set_tooltip_text(str(action.title))
                button.connect("activate-link", action_context_revoked)
            else:
                button = action_button(str(action.title), str(action.icon_name or "view-more-symbolic"))
                callback = getattr(action, "callback", None)
                if callable(callback):
                    def activate(clicked, cb=callback, action_id=str(action.id)):
                        if action_context_revoked(clicked):
                            return
                        # Capture the real clicked action before its callback
                        # switches routes: mouse clicks need not give GTK focus.
                        window = self._window
                        youth = getattr(window, "_plugin_page_widgets", {}).get("youth")
                        intent = (
                            action_id == "search-library"
                            and not getattr(window, "_closed", False)
                            and window.view_stack.get_visible_child_name() == "youth"
                            and youth is not None and clicked.is_ancestor(youth)
                            and clicked.get_mapped() and clicked.get_sensitive()
                        )
                        if intent:
                            identity = getattr(
                                getattr(window.service, "current_identity", None),
                                "identity_id", None,
                            )
                            window._youth_return_focus = (clicked, identity)
                        result = cb()
                        if intent and result is False:
                            window._youth_return_focus = None
                        return result
                    button.connect("clicked", activate)
            from cifereca.gui.detail_action_state import apply_detail_action_state
            apply_detail_action_state(button, action)
            button._cifereca_action_id = str(action.id)
            buttons.append(button)
        panel.add_actions(buttons)
        if pinned is not None:
            from cifereca.gui.plugin_public_c_guard import request_plugin_public_web_action
            request_plugin_public_web_action(self, panel, pinned)

    def show_existing_search_detail(
        self, item, detail, *, context_sections=(), public_c_document=None,
        c_selection_is_current=None, public_c_minimum_revision=None,
    ) -> None:
        """Render Core's actual Search detail/actions in this plugin panel.

        The supplier result comes only from Core. Preserve contextual reasons
        from the recommendation as *additional* read-only fields; none may
        influence a borrowing or acquisition decision.
        """
        panel = self._page.detail
        pinned = None
        from .recommendation_exact_cover import loaded_recommendation_cover
        proof = loaded_recommendation_cover(self._window, item, detail)
        if proof is None:
            # Preserve the legacy renderer ABI for all non-C paths.
            # Only the attested known-v2 worker passes an exact C ref.
            if public_c_document is None:
                panel.show_item_detail(item, detail)
            else:
                from .plugin_public_c_guard import staged_plugin_exact_public_c
                admitted, stage = staged_plugin_exact_public_c(
                    self, panel, public_c_document,
                    selection_is_current=c_selection_is_current,
                    minimum_revision=public_c_minimum_revision,
                )
                if admitted:
                    if callable(c_selection_is_current) and type(public_c_minimum_revision) is int:
                        from .plugin_public_c_guard import selected_plugin_public_c
                        pinned = selected_plugin_public_c(self, public_c_document)
                    with stage:
                        panel.show_public_c_search_document(
                            item, detail=detail,
                            public_c_document=public_c_document,
                        )
                else:
                    # A stale async completion must not overwrite even
                    # the NEW selection's existing detail with an empty pane.
                    # Neither old image nor old source/account actions run.
                    return
        else:
            panel.show_search_document(item, detail=detail, cover_evidence=proof)
        # Search appends these same Core local/open/send/owned actions after
        # the same renderer. Rights remain with the common controller.
        from .query_model import result_row
        self._window._add_local_book_actions(panel, result_row(item))
        self._append_recommendation_context(panel, context_sections)
        if pinned is not None:
            from .plugin_public_c_guard import request_plugin_public_web_action
            request_plugin_public_web_action(
                self, panel, pinned, expected_epoch=public_c_minimum_revision,
                selection_is_current=c_selection_is_current,
            )

    def show_existing_search_unavailable(
        self, item, *, context_sections=(), public_c_document=None,
        c_selection_is_current=None, public_c_minimum_revision=None,
    ) -> None:
        """Match Search's non-mutating detail fallback after holdings failure.

        Only the exact-detail Core port invokes this after the user's click
        and a fresh identity/source/selection lease check. Availability is
        explicitly unverified; local/owned actions reuse Search's admission.
        """
        panel = self._page.detail
        pinned = None
        from .recommendation_exact_cover import loaded_recommendation_cover
        proof = loaded_recommendation_cover(self._window, item)
        if proof is None:
            if public_c_document is None:
                panel.show_search(item, loading=False)
            else:
                from .plugin_public_c_guard import staged_plugin_exact_public_c
                admitted, stage = staged_plugin_exact_public_c(
                    self, panel, public_c_document,
                    selection_is_current=c_selection_is_current,
                    minimum_revision=public_c_minimum_revision,
                )
                if admitted:
                    if callable(c_selection_is_current) and type(public_c_minimum_revision) is int:
                        from .plugin_public_c_guard import selected_plugin_public_c
                        pinned = selected_plugin_public_c(self, public_c_document)
                    with stage:
                        panel.show_public_c_search_document(
                            item, loading=False,
                            public_c_document=public_c_document,
                        )
                else:
                    # The offline callback is equally stale. Leave the
                    # current selection's panel untouched (not even empty).
                    return
        else:
            panel.show_search_document(item, cover_evidence=proof)
        from .query_model import result_row
        self._window._add_local_book_actions(panel, result_row(item))
        self._append_recommendation_context(panel, context_sections)
        if pinned is not None:
            from .plugin_public_c_guard import request_plugin_public_web_action
            request_plugin_public_web_action(
                self, panel, pinned, expected_epoch=public_c_minimum_revision,
                selection_is_current=c_selection_is_current,
            )

    def _append_recommendation_context(self, panel, context_sections) -> None:
        """Read-only recommendation explanations, identical in both states."""
        from ..book_detail_metadata import safe_detail_value
        from ..plugin_api.models import DetailField, DetailSection
        for section in context_sections[:16]:
            if type(section) is not DetailSection:
                continue
            if (type(section.title) is not str
                    or not 0 < len(section.title) <= 120):
                continue
            fields = []
            for field in section.fields[:128]:
                if type(field) is not DetailField or type(field.label) is not str:
                    continue
                if not 0 < len(field.label) <= 120:
                    continue
                value = safe_detail_value(field.value)
                if not value or len(value) > 2048:
                    continue
                fields.append((field.label, value, field))
            if fields:
                panel._group(
                    section.title,
                    [(label, value) for label, value, _field in fields],
                    fields=tuple(field for _label, _value, field in fields),
                )

    def empty_detail(
        self,
        title: str = "Aucun document sélectionné",
        description: str = "Sélectionnez un document pour afficher ses détails.",
    ) -> None:
        self._revoke_pending_exact_detail()
        self._page.detail.empty(title, description)

