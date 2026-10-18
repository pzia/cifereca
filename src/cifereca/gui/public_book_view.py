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
            ui_rows.append(BookListItem(
                presented_key,
                str(row.title or "Sans titre"),
                str(row.subtitle or ""),
                str(row.meta or ""),
                str(row.badge or ""),
                getattr(row, "thumbnail_url", None),
            ))
        # Publish mapping before GTK selection notifications during show_rows.
        self._rows = mapped
        self._page.show_rows(ui_rows, total_count=total_count)

    def set_list_state(self, title: str, description: str = "", icon_name: str = "system-search-symbolic") -> None:
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

    def empty_detail(
        self,
        title: str = "Aucun document sélectionné",
        description: str = "Sélectionnez un document pour afficher ses détails.",
    ) -> None:
        self._page.detail.empty(title, description)

