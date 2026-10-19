"""UI-only builder of the application shell (#1130).

The MainWindow remains the sole owner of sidebar/stack/activity GTK widgets.
Widgets, CSS functions and the package version are injected. No providers,
subscriptions or background work are activated by this builder.
"""
from __future__ import annotations

from .navigation_history import NavigationHistory


def build_main_shell(window, *, Gtk, add_css, version):
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    window.shell_root = root
    window._navigation_history = NavigationHistory()
    window._restoring_navigation = False
    window.shell_header = window._create_shell_header()
    root.append(window.shell_header)

    body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    body.set_vexpand(True)
    root.append(body)
    sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    window.sidebar = sidebar
    sidebar.set_size_request(160, -1)
    add_css(sidebar, "sidebar")
    body.append(sidebar)
    brand = Gtk.Label(label=f"v{version}", xalign=0)
    brand.set_margin_top(14); brand.set_margin_start(16); brand.set_margin_bottom(4)
    add_css(brand, "caption", "dim-label")
    sidebar.append(brand)
    window.nav_list = Gtk.ListBox()
    window.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
    window.nav_list.set_activate_on_single_click(True)
    window.nav_list.connect("row-selected", window._nav_selected)
    sidebar.append(window.nav_list)
    window.nav_rows: dict[str, Gtk.ListBoxRow] = {}
    window._nav_groups = {}
    window._nav_group_expanded = {}
    window.view_stack = Gtk.Stack()
    # #2027 measurement experiment: isolate crossfade measurement in the
    # full-window GtkStack; page routing and visibility stay unchanged.
    window.view_stack.set_transition_type(Gtk.StackTransitionType.NONE)
    window.view_stack.set_hhomogeneous(False)
    window.view_stack.set_hexpand(True); window.view_stack.set_vexpand(True)
    body.append(window.view_stack)
    window._rebuild_plugin_pages()

    window.busy_revealer = Gtk.Revealer()
    window.busy_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
    busy = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
    add_css(busy, "busy-bar")
    window.busy_spinner = Gtk.Spinner(); window.busy_spinner.set_visible(False); busy.append(window.busy_spinner)
    window.activity_status_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
    window.activity_status_icon.set_visible(False); busy.append(window.activity_status_icon)
    window.busy_label = Gtk.Label(label="", xalign=0)
    window.busy_label.set_hexpand(True); window.busy_label.set_wrap(True); busy.append(window.busy_label)

    window.activity_history_button = Gtk.MenuButton(label="Historique")
    window.activity_history_button.set_icon_name("view-list-symbolic")
    window.activity_history_button.set_tooltip_text("Historique des opérations")
    window.activity_history_button.set_visible(False)
    history_popover = Gtk.Popover()
    window.activity_history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    window.activity_history_box.set_margin_top(8); window.activity_history_box.set_margin_bottom(8)
    window.activity_history_box.set_margin_start(10); window.activity_history_box.set_margin_end(10)
    history_popover.set_child(window.activity_history_box)
    window.activity_history_button.set_popover(history_popover)
    busy.append(window.activity_history_button)

    window.activity_dismiss = Gtk.Button(label="Acquitter")
    window.activity_dismiss.set_tooltip_text("Acquitter les erreurs affichées (historique conservé)")
    window.activity_dismiss.set_visible(False)
    window.activity_dismiss.connect("clicked", lambda _button: window._dismiss_activity_feedback())
    busy.append(window.activity_dismiss)
    root.append(window.busy_revealer); window.busy_revealer.set_child(busy)

    window.navigate("dashboard")
    return root
