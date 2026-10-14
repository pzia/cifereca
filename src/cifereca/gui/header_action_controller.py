"""GTK-independent adapter for native titlebar and page action menus (#1130).

The window owns widgets, public plugin action contexts, and GTK action groups.
GTK/Gio and pure shell_model functions are injected by the MainWindow facade.
"""
from __future__ import annotations


def refresh_header_actions(window, route, *, Gtk, Gio, clear_box, validate_action_selection, visible_actions, menu_sections):
    clear_box(window.header_actions)
    defaults = ["search.open", "library.open", "library.import-acsm", "settings.open", "plugins.open"]
    try:
        selected = window._titlebar_service.selected_actions() if window._titlebar_service else defaults
    except Exception:
        selected = defaults
    selected, obsolete = validate_action_selection(window._pages.values(), selected)
    previous_obsolete = getattr(window, "_obsolete_titlebar_actions", ())
    if obsolete and obsolete != previous_obsolete:
        window.diagnostics.add(
            "UI",
            "titlebar action configuration",
            "warning",
            detail={"obsolete_action_ids": list(obsolete)},
            level="normal",
        )
    window._obsolete_titlebar_actions = obsolete
    host = window._UiHost(window)
    for key, action in visible_actions(window._pages.values(), route, selected):
        button = Gtk.Button.new_from_icon_name(action.icon_name); button.set_tooltip_text(action.title)
        button.connect("clicked", lambda _b, a=action: window._invoke_ui_action(a, host))
        window.header_actions.append(button)
    model = Gio.Menu()
    group = Gio.SimpleActionGroup()
    count = 0
    for title, entries in menu_sections(window._pages.values(), route):
        section = Gio.Menu()
        for label, action in entries:
            name = f"item{count}"; count += 1
            command = Gio.SimpleAction.new(name, None)
            command.connect("activate", lambda _a, _p, a=action: window._invoke_ui_action(a, host))
            group.add_action(command)
            section.append(label, f"pagemenu.{name}")
        model.append_section(title, section)
    tools = Gio.Menu()
    tools.append("Reconnecter les sources", "win.reconnect-sources")
    tools.append("Raccourcis clavier", "win.shortcuts")
    tools.append("Quitter", "win.quit")
    model.append_section(None, tools)
    window.insert_action_group("pagemenu", group)
    window._page_menu_actions = group
    window.shell_menu.set_menu_model(model)
    window._ensure_pages_menu_rightmost()
