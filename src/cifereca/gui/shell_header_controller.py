"""Shell titlebar, navigation buttons and compact selector widget factory (#1130).

All GTK, Adw, Gio and Pango dependencies are injected by the MainWindow
facade. The owning window retains plugin services, action contexts, selection
callbacks, navigation history and all widgets. This module has no GTK import
and does not subscribe to services or start network work.
"""
from __future__ import annotations

def create_shell_header(window, *, Gtk, Adw, Gio, Pango):
    host = window._UiHost(window)
    window._titlebar_service = None
    if window.plugin_manager:
        try:
            selected = window.plugin_manager.select("ui.titlebar")
            window._titlebar_service = window.plugin_manager.service(selected.descriptor.id) if selected else None
        except Exception:
            window._titlebar_service = None
    try:
        header = window._titlebar_service.build(host) if window._titlebar_service else Adw.HeaderBar()
        if not isinstance(header, Adw.HeaderBar): raise TypeError("Titlebar must return Adw.HeaderBar")
    except Exception as exc:
        window.diagnostics.add("PLUGIN", "titlebar build", "error", detail={"error": str(exc)})
        header = Adw.HeaderBar(); window._titlebar_service = None
    window.header_title = Gtk.Label(label="cifereca")
    window.header_subtitle = Gtk.Label(label="")
    title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    title.append(window.header_title); title.append(window.header_subtitle); header.set_title_widget(title)
    navigation = Gtk.Box(spacing=2)
    window.back_button = Gtk.Button(icon_name="go-previous-symbolic")
    window.back_button.set_tooltip_text("Précédent (Alt+Gauche)")
    window.back_button.connect("clicked", lambda _b: window.go_back())
    window.forward_button = Gtk.Button(icon_name="go-next-symbolic")
    window.forward_button.set_tooltip_text("Suivant (Alt+Droite)")
    window.forward_button.connect("clicked", lambda _b: window.go_forward())
    navigation.append(window.back_button); navigation.append(window.forward_button)
    header.pack_start(navigation)
    window.header_actions = Gtk.Box(spacing=4); header.pack_start(window.header_actions)
    updater = getattr(window, "_update_navigation_buttons", None)
    if callable(updater): updater()
    def compact_dropdown(tooltip: str) -> Gtk.DropDown:
        dropdown = Gtk.DropDown()
        factory = Gtk.SignalListItemFactory()
        def setup(_factory, list_item):
            label = Gtk.Label(xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_single_line_mode(True)
            list_item.set_child(label)
        def bind(_factory, list_item):
            item = list_item.get_item()
            label = list_item.get_child()
            text = item.get_string() if isinstance(item, Gtk.StringObject) else str(item or "")
            label.set_label(text)
            label.set_tooltip_text(text)
        factory.connect("setup", setup)
        factory.connect("bind", bind)
        dropdown.set_factory(factory)

        list_factory = Gtk.SignalListItemFactory()
        def setup_list(_factory, list_item):
            label = Gtk.Label(xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.set_single_line_mode(True)
            label.set_max_width_chars(44)
            list_item.set_child(label)
        list_factory.connect("setup", setup_list)
        list_factory.connect("bind", bind)
        dropdown.set_list_factory(list_factory)
        dropdown.set_tooltip_text(tooltip)
        return dropdown

    window.identity_dropdown = compact_dropdown("Identité active")
    window.identity_dropdown.connect("notify::selected", window._identity_changed)
    window.account_dropdown = compact_dropdown("Délégations de l’identité active")
    window.account_dropdown.connect("notify::selected", window._account_changed)
    window.pages_menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
    window.pages_menu_button.set_tooltip_text("Menu des pages (F10)")
    window.shell_menu = Gtk.PopoverMenu.new_from_model(Gio.Menu())
    window.pages_menu_button.set_popover(window.shell_menu)
    # One ordered box makes the menu the rightmost application control.
    controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    window.statistics_button=Gtk.Button(label="— livres · — prêts")
    window.statistics_button.add_css_class('flat')
    window.statistics_button.set_tooltip_text("Statistiques de l’identité : inventaire local et derniers prêts connus")
    window.statistics_button.connect('clicked',lambda _b:window._show_statistics())
    controls.append(window.identity_dropdown); controls.append(window.account_dropdown)
    controls.append(window.statistics_button)
    controls.append(window.pages_menu_button)
    window._header_controls = controls
    header.pack_end(controls)
    window._ensure_pages_menu_rightmost()
    return header

