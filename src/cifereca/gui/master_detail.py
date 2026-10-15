from __future__ import annotations
from typing import Callable, TYPE_CHECKING
import time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk
from .book_widgets import BookListItem, BookListCell
from .detail_panel import DetailPanel
from .action_widgets import add_css, AdaptiveActionBar
from .layout import MASTER_DETAIL_NARROW_WINDOW_WIDTH, master_detail_is_narrow
if TYPE_CHECKING:
    from .main_window import MainWindow


class MasterDetailPage(Gtk.Box):
    """Page maître/détail basée sur Gtk.ListView virtualisé."""

    def __init__(self, window: "MainWindow", title: str, subtitle: str, empty_title: str, empty_desc: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.title = title
        self._selected_callbacks: list[Callable[[BookListItem | None], None]] = []

        # Keep headings independent from action controls.  On 1024px-wide
        # windows the previous single horizontal row forced search/filter
        # controls to compete with the page title and description.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.page_header = header
        header.set_margin_top(16)
        header.set_margin_start(18)
        header.set_margin_end(18)
        headings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        headings.set_hexpand(True)
        header.append(headings)
        heading = Gtk.Label(label=title, xalign=0); add_css(heading, "page-title"); headings.append(heading)
        if subtitle:
            desc = Gtk.Label(label=subtitle, xalign=0); desc.set_wrap(True); add_css(desc, "dim-label"); headings.append(desc)
        self.count_label = Gtk.Label(label="", xalign=1)
        self._count_total_only = False
        add_css(self.count_label, "caption", "dim-label")
        header.append(self.count_label)
        self.header_actions = AdaptiveActionBar(source=header)
        self.header_actions.set_halign(Gtk.Align.END); self.header_actions.set_valign(Gtk.Align.START)
        header.append(self.header_actions)
        self.append(header)

        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.toolbar.set_margin_top(10)
        self.toolbar.set_margin_bottom(12)
        self.toolbar.set_margin_start(18)
        self.toolbar.set_margin_end(18)
        self.toolbar.set_hexpand(True)
        self.append(self.toolbar)

        self.detail_back = Gtk.Button(label="Retour à la liste")
        self.detail_back.set_tooltip_text("Revenir à la liste des documents")
        self.detail_back.set_halign(Gtk.Align.START)
        self.detail_back.set_margin_start(18)
        self.detail_back.set_margin_bottom(8)
        self.detail_back.set_visible(False)
        self.detail_back.connect("clicked", lambda _button: self._show_narrow_list())
        self.append(self.detail_back)

        self.paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        self.paned.set_wide_handle(True)
        self.paned.set_position(440)
        self.paned.set_shrink_start_child(True)
        self.paned.set_shrink_end_child(True)
        self.paned.set_vexpand(True)
        self._narrow_master_detail = False
        self._narrow_detail_visible = False
        self._narrow_sidebar_restore_visible: bool | None = None
        self._narrow_breakpoint = MASTER_DETAIL_NARROW_WINDOW_WIDTH

        def adapt_paned_position(paned, _pspec=None) -> None:
            max_position = int(paned.get_property("max-position"))
            if max_position <= 0:
                return
            try:
                window_width = int(self.window.get_width())
            except (AttributeError, TypeError, ValueError):
                window_width = 0
            narrow = master_detail_is_narrow(
                window_width,
                breakpoint=self._narrow_breakpoint,
            )
            if narrow != self._narrow_master_detail:
                self._narrow_master_detail = narrow
                if not narrow:
                    self._narrow_detail_visible = False
                    self._set_narrow_detail_context(False)
                    self.detail_back.set_visible(False)
            if self._narrow_master_detail:
                paned.set_position(0 if self._narrow_detail_visible else max_position)
                self.detail_back.set_visible(self._narrow_detail_visible)
            else:
                paned.set_position(min(440, max(0, max_position - 360)))

        self.paned.connect("notify::max-position", adapt_paned_position)
        self.append(self.paned)

        self.list_stack = Gtk.Stack()
        self.list_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.list_stack.set_hexpand(True)
        self.list_stack.set_vexpand(True)
        self.empty_page = Adw.StatusPage()
        self.empty_page.set_title(empty_title)
        self.empty_page.set_description(empty_desc)
        self.empty_page.set_icon_name("accessories-dictionary-symbolic")
        self.list_stack.add_named(self.empty_page, "empty")

        self.store = Gio.ListStore.new(BookListItem)
        self.selection = Gtk.SingleSelection.new(self.store)
        self.selection.set_autoselect(False)
        self.selection.set_can_unselect(True)
        self.selection.connect("notify::selected-item", self._selection_changed)

        self.factory = Gtk.SignalListItemFactory()
        self.factory.connect("setup", self._factory_setup)
        self.factory.connect("bind", self._factory_bind)
        self.factory.connect("unbind", self._factory_unbind)

        self.listview = Gtk.ListView.new(self.selection, self.factory)
        self.listview.set_single_click_activate(False)
        self.listview.set_hexpand(True)
        self.listview.set_halign(Gtk.Align.FILL)
        self.scroll = Gtk.ScrolledWindow()
        # The list viewport must own horizontal allocation. GTK_POLICY_NEVER
        # lets child natural width drive the scroller, which makes pathological
        # row metadata capable of shifting the whole list outside the viewport.
        self.scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_width(False)
        self.scroll.set_min_content_width(0)
        self.scroll.set_hexpand(True)
        self.scroll.set_vexpand(True)
        self.scroll.set_child(self.listview)
        self.list_stack.add_named(self.scroll, "list")
        self.paned.set_start_child(self.list_stack)
        self.detail = DetailPanel(window)
        self.paned.set_end_child(self.detail)
        self.listview.connect("activate", lambda _view, _position: self.detail.child_focus(Gtk.DirectionType.TAB_FORWARD))
        keys = Gtk.EventControllerKey()
        def key_pressed(_controller, keyval, _keycode, state):
            if keyval == Gdk.KEY_Escape:
                if self._narrow_master_detail and self._narrow_detail_visible:
                    self._show_narrow_list()
                else:
                    self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
                    self.listview.grab_focus()
                return True
            if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_f, Gdk.KEY_F):
                controls = getattr(window, "library_controls" if title == "Bibliothèque" else "search_controls", None)
                if controls: controls.entry.grab_focus(); return True
            return False
        keys.connect("key-pressed", key_pressed); self.add_controller(keys)
        self.list_stack.set_visible_child_name("empty")
        # A narrow detail may hide shell navigation. Restore it while this page
        # is unmapped, then re-apply the detail context if the user comes back.
        self.connect("unmap", lambda _widget: self._set_narrow_detail_context(False))
        self.connect("map", lambda _widget: self._set_narrow_detail_context(self._narrow_detail_visible))

    def _factory_setup(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        list_item.set_child(BookListCell(self.window))

    def _factory_bind(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        cell = list_item.get_child()
        item = list_item.get_item()
        if isinstance(cell, BookListCell) and isinstance(item, BookListItem):
            cell.bind(item)

    def _factory_unbind(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        cell = list_item.get_child()
        if isinstance(cell, BookListCell):
            cell.unbind()

    def connect_selected(self, callback: Callable[[BookListItem | None], None]) -> None:
        self._selected_callbacks.append(callback)

    def _selection_changed(self, _selection: Gtk.SingleSelection, _pspec) -> None:
        item = self.selection.get_selected_item()
        selected = item if isinstance(item, BookListItem) else None
        if selected is None:
            self.detail.empty("Aucun document sélectionné", "Sélectionnez un document pour afficher ses détails.")
        for callback in self._selected_callbacks:
            callback(selected)
        if self._narrow_master_detail and selected is not None:
            self._show_narrow_detail()

    def select_key(self, key: str) -> bool:
        """Select a visible row by stable key and reveal it without changing filters."""
        target = str(key)
        for index in range(self.store.get_n_items()):
            row = self.store.get_item(index)
            if isinstance(row, BookListItem) and row.key == target:
                self.selection.set_selected(index)
                try:
                    self.listview.scroll_to(index, Gtk.ListScrollFlags.FOCUS, None)
                except (AttributeError, TypeError):
                    pass
                return True
        return False

    def _set_narrow_detail_context(self, active: bool) -> None:
        """Reserve the narrow page for the active detail without losing list state."""
        self.page_header.set_visible(not active)
        self.toolbar.set_visible(not active)
        sidebar = getattr(self.window, "sidebar", None)
        if sidebar is None:
            return
        if active:
            if self._narrow_sidebar_restore_visible is None:
                self._narrow_sidebar_restore_visible = bool(sidebar.get_visible())
            sidebar.set_visible(False)
            return
        restore = self._narrow_sidebar_restore_visible
        self._narrow_sidebar_restore_visible = None
        if restore is not None:
            sidebar.set_visible(bool(restore))

    def _show_narrow_detail(self) -> None:
        if not self._narrow_master_detail:
            return
        self._narrow_detail_visible = True
        self._set_narrow_detail_context(True)
        self.detail_back.set_visible(True)
        self.paned.set_position(0)
        self.detail.child_focus(Gtk.DirectionType.TAB_FORWARD)

    def _show_narrow_list(self) -> None:
        if not self._narrow_master_detail:
            return
        self._narrow_detail_visible = False
        self._set_narrow_detail_context(False)
        self.detail_back.set_visible(False)
        max_position = int(self.paned.get_property("max-position"))
        if max_position > 0:
            self.paned.set_position(max_position)
        self.listview.grab_focus()

    def clear_rows(self) -> None:
        self.selection.set_selected(0xFFFFFFFF)
        try:
            self.store.remove_all()
        except Exception:
            while self.store.get_n_items() > 0:
                self.store.remove(self.store.get_n_items() - 1)
        self.count_label.set_label("")
        self._total_count = 0
        # Never keep stale metadata from a row that belonged to the previous
        # account/provider/filter. This is especially visible when switching
        # to an anonymous catalogue account with no selected loan.
        self.detail.empty("Aucun document sélectionné", "Sélectionnez un document dans la liste.")

    def set_total_only_count(self, enabled: bool = True) -> None:
        self._count_total_only = bool(enabled)
        self._update_count()

    @staticmethod
    def _item_count_label(count: int) -> str:
        return f"{count} élément" if count == 1 else f"{count} éléments"

    def _update_count(self, total_count: int | None = None) -> None:
        displayed = self.store.get_n_items()
        total = int(total_count if total_count is not None else getattr(self, "_total_count", displayed))
        self._total_count = max(total, displayed)
        if not displayed:
            self.count_label.set_label("")
        elif hasattr(self, "_count_total_only") and self._count_total_only:
            self.count_label.set_label(self._item_count_label(self._total_count))
        elif self._total_count > displayed:
            self.count_label.set_label(f"{displayed} / {self._total_count} élément(s)")
        else:
            self.count_label.set_label(f"{displayed} élément(s)")

    def show_rows(self, rows: list[BookListItem], total_count: int | None = None) -> None:
        started = time.perf_counter()
        selected = self.selection.get_selected_item()
        selected_key = selected.key if isinstance(selected, BookListItem) else None
        existing_count = self.store.get_n_items()
        existing_keys = [self.store.get_item(index).key for index in range(existing_count)]
        new_keys = [row.key for row in rows]

        # Progressive search normally grows by appending provider batches. Keep
        # the already-materialised GTK rows in place instead of splicing the
        # complete ListStore on every batch. The final authoritative ranking may
        # reorder rows; that falls through to the full reconciliation below.
        if existing_count and existing_keys == new_keys[:existing_count] and len(rows) >= existing_count:
            tail = rows[existing_count:]
            if tail:
                try:
                    self.store.splice(existing_count, 0, tail)
                except Exception:
                    for row in tail:
                        self.store.append(row)
            self._update_count(total_count if total_count is not None else len(rows))
            self.list_stack.set_visible_child_name("list" if rows else "empty")
            if hasattr(self.window, "ui_perf"):
                self.window.ui_perf.record("list.append-fast", (time.perf_counter() - started) * 1000.0)
            return

        self.selection.set_selected(0xFFFFFFFF)
        try:
            self.store.splice(0, self.store.get_n_items(), rows)
        except Exception:
            self.clear_rows()
            for row in rows:
                self.store.append(row)
        self._update_count(total_count if total_count is not None else len(rows))
        self.list_stack.set_visible_child_name("list" if rows else "empty")
        if rows and selected_key is not None:
            restored = next((index for index, row in enumerate(rows) if row.key == selected_key), None)
            if restored is not None:
                self.selection.set_selected(restored)
        if hasattr(self.window, "ui_perf"):
            self.window.ui_perf.record("list.replace", (time.perf_counter() - started) * 1000.0)

    def append_rows(self, rows: list[BookListItem], total_count: int | None = None) -> None:
        started = time.perf_counter()
        if rows:
            try:
                self.store.splice(self.store.get_n_items(), 0, rows)
            except Exception:
                for row in rows:
                    self.store.append(row)
        self._update_count(total_count)
        if self.store.get_n_items() > 0:
            self.list_stack.set_visible_child_name("list")
        if hasattr(self.window, "ui_perf"):
            self.window.ui_perf.record("list.append", (time.perf_counter() - started) * 1000.0)

