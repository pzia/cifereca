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
            item._public_row_content = presentation
            ui_rows.append(item)
        # Publish mapping before GTK selection notifications during show_rows.
        self._rows = mapped
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
        panel.show_presentation(detail)
        buttons = []
        for action in getattr(detail, "actions", ()) or ():
            raw_uri = str(getattr(action, "uri", "") or "")
            if raw_uri:
                # Plugin actions share the same URL security boundary as
                # document links. Drop invalid URI actions completely.
                uri = safe_document_link_url(raw_uri, relation="related")
                if not uri:
                    continue
                button = Gtk.LinkButton(uri=uri, label=str(action.title))
                button.set_tooltip_text(str(action.title))
            else:
                button = action_button(str(action.title), str(action.icon_name or "view-more-symbolic"))
                callback = getattr(action, "callback", None)
                if callable(callback):
                    def activate(clicked, cb=callback, action_id=str(action.id)):
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
            button._cifereca_action_id = str(action.id)
            buttons.append(button)
        panel.add_actions(buttons)

    def show_existing_search_detail(
        self, item, detail, *, context_sections=(),
    ) -> None:
        """Render Core's actual Search detail/actions in this plugin panel.

        The supplier result comes only from Core. Preserve contextual reasons
        from the recommendation as *additional* read-only fields; none may
        influence a borrowing or acquisition decision.
        """
        panel = self._page.detail
        panel.show_item_detail(item, detail)
        # Search appends these same Core local/open/send/owned actions after
        # the same renderer. Rights remain with the common controller.
        from .query_model import result_row
        self._window._add_local_book_actions(panel, result_row(
            item, purchase_evidence=getattr(self._window, "_search_purchase_evidence", None),
            identity_id=getattr(getattr(getattr(self._window, "service", None),
                                        "current_identity", None), "identity_id", None),
            account_context_epoch=getattr(getattr(self._window, "service", None),
                                           "_account_context_epoch", None),
            source_context_epoch=getattr(getattr(self._window, "service", None),
                                          "_source_context_epoch", None),
        ))
        self._append_recommendation_context(panel, context_sections)

    def show_existing_search_unavailable(
        self, item, *, context_sections=(),
    ) -> None:
        """Match Search's non-mutating detail fallback after holdings failure.

        Only the exact-detail Core port invokes this after the user's click
        and a fresh identity/source/selection lease check. Availability is
        explicitly unverified; local/owned actions reuse Search's admission.
        """
        panel = self._page.detail
        panel.show_search(item, loading=False)
        from .query_model import result_row
        self._window._add_local_book_actions(panel, result_row(
            item, purchase_evidence=getattr(self._window, "_search_purchase_evidence", None),
            identity_id=getattr(getattr(getattr(self._window, "service", None),
                                        "current_identity", None), "identity_id", None),
            account_context_epoch=getattr(getattr(self._window, "service", None),
                                           "_account_context_epoch", None),
            source_context_epoch=getattr(getattr(self._window, "service", None),
                                          "_source_context_epoch", None),
        ))
        self._append_recommendation_context(panel, context_sections)

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

