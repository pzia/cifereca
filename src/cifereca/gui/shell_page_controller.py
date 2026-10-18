"""Page contribution and navigation rebuild coordinator (#1130).

MainWindow owns GTK, plugin identities and view state. The GTK binding and
shell helpers are injected to keep this module importable without PyGObject.
"""
from __future__ import annotations

def rebuild_plugin_pages(window, *, Gtk, Pango, clear_box, resolve_pages) -> None:
    # A page factory or a plugin manager hook can synchronously dispose the
    # window or trigger another rebuild. Its abandoned invocation must never
    # continue mutating navigation, restoring filters or selecting a route.
    if getattr(window, "_closed", False):
        return
    token = object()
    window._shell_page_rebuild_token = token

    def is_current():
        return (
            not getattr(window, "_closed", False)
            and getattr(window, "_shell_page_rebuild_token", None) is token
        )

    window._overview_listeners = []
    if not hasattr(window, "view_stack"):
        return
    query_states = {route: controls.state() for route in ("search", "library")
                    if (controls := getattr(window, route + "_controls", None)) is not None}
    if not is_current():
        return
    previous = window.view_stack.get_visible_child_name() or "dashboard"
    if not is_current():
        return
    for widget in tuple(window._plugin_page_widgets.values()):
        window.view_stack.remove(widget)
        if not is_current():
            return
    window._plugin_page_widgets.clear()
    clear_box(window.nav_list)
    if not is_current():
        return
    window.nav_rows.clear()
    window._nav_groups.clear()
    from ..builtin_ui.cifereca_builtin_ui import Pages
    from dataclasses import replace
    fallback = [replace(page, priority=-10000) for page in Pages().pages()]
    try:
        contributed = window.plugin_manager.page_contributions() if window.plugin_manager else []
    except Exception:
        contributed = []
    if not is_current():
        return
    if not contributed:
        contributed = list(fallback)
    # Management pages remain reachable to recover from broken/disabled UI.
    contributed += [p for p in fallback if p.route in {"plugins", "settings"} and all(x.route != p.route for x in contributed)]
    resolved = resolve_pages(contributed)
    if not is_current():
        return
    window._pages = {}
    host = window._UiHost(window)
    if not is_current():
        return
    group = None
    for page in resolved:
        if not is_current():
            return
        try:
            widget = page.factory(host)
            if not is_current():
                return
            if not isinstance(widget, Gtk.Widget):
                raise TypeError("Page factory must return Gtk.Widget")
        except Exception:
            if not is_current():
                return
            # The route and exception originate from a plugin contribution;
            # neither is safe for a diagnostic archive.
            window.diagnostics.add(
                "PLUGIN", "ui.page build", "error",
                detail={"code": "page_factory_failed"},
            )
            if not is_current():
                return
            recovery = next((p for p in fallback if p.route == page.route and p.route in {"plugins", "settings"}), None)
            if recovery is None:
                continue
            widget = recovery.factory(host)
            if not is_current():
                return
            page = recovery
        window.view_stack.add_named(widget, page.route)
        if not is_current():
            return
        window._plugin_page_widgets[page.route] = widget
        window._pages[page.route] = page
        if page.group != group:
            group = page.group
            expanded = window._nav_group_expanded.setdefault(group, group != "configuration")
            group_label = {"usage": "Pages", "configuration": "Configuration / Technique"}.get(group, group)
            button = Gtk.ToggleButton()
            button_label = Gtk.Label(label=group_label, xalign=0)
            button_label.set_ellipsize(Pango.EllipsizeMode.END)
            button.set_child(button_label)
            button.set_active(expanded)
            button.set_tooltip_text(f"{group_label} — Déplier / replier la rubrique")
            # Old group buttons can still emit a late GTK signal during rebuild.
            # Their callbacks only belong to the rebuild that created them.
            def toggled(source, owner_group=group):
                if is_current():
                    window._toggle_nav_group(owner_group, source.get_active())
            button.connect("toggled", toggled)
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_activatable(False)
            row.set_child(button)
            window.nav_list.append(row)
            if not is_current():
                return
        window._add_nav_row(page.route, page.title, page.icon_name)
        if not is_current():
            return
        window._nav_groups[page.route] = group
        window.nav_rows[page.route].set_visible(window._nav_group_expanded[group])
    for route, state in query_states.items():
        if not is_current():
            return
        controls = getattr(window, route + "_controls", None)
        if route in window._pages and controls is not None:
            controls.set_state(state)
    if is_current() and window._pages:
        window.navigate(previous if previous in window._pages else next(iter(window._pages)))


def build_standard_page(window, route, *, Gtk, PluginManagerPage):
    builders = {"dashboard": window._build_dashboard_page, "loans": window._build_loans_page,
                "search": window._build_search_page, "history": window._build_history_page,
                "bookings": window._build_bookings_page, "library": window._build_library_page,
                "recent": window._build_recent_page, "sync": window._build_sync_page,
                "tools": window._build_tools_page, "settings": window._build_settings_page,
                "plugins": lambda: PluginManagerPage(window, window.plugin_manager, window._plugins_changed)
                if window.plugin_manager else Gtk.Label(label="Gestionnaire indisponible")}
    widget = builders[route]()
    attr = {"loans": "loan", "bookings": "booking"}.get(route, route)
    setattr(window, attr + "_page", widget)
    if route == "library": window.kobo_page = widget
    return widget
