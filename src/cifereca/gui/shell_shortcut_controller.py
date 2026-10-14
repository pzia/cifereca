"""CoreUI #1130: GTK-independent keyboard and application action dispatcher.

MainWindow owns the GTK action group, widgets, and public methods. Only Gio
action creation and Adw shortcut dialog creation are passed explicitly.
No plugin subscriptions, implicit source connections, or additional navigation.
"""
from __future__ import annotations

from .shortcuts import SHORTCUTS, adjacent_route, shortcuts_text


def install_shell_shortcuts(window, *, Gio) -> None:
    if getattr(window, "_closed", False):
        return
    callbacks = {
        "quit": window._quit_application,
        "close": window.close,
        "pages-menu": window._open_pages_menu,
        "focus-search": window._focus_search,
        "settings": window._show_settings,
        "next-page": lambda: window._cycle_page(1),
        "previous-page": lambda: window._cycle_page(-1),
        "go-back": lambda: getattr(window, "go_back", lambda: None)(),
        "go-forward": lambda: getattr(window, "go_forward", lambda: None)(),
        "shortcuts": window._show_shortcuts,
        "reconnect-sources": window.reconnect_sources,
    }
    for name, callback in callbacks.items():
        action = Gio.SimpleAction.new(name, None)
        # An already-queued action must not reach widgets or providers after
        # the owning MainWindow has closed.
        action.connect(
            "activate",
            lambda _action, _parameter, cb=callback: (
                cb() if not getattr(window, "_closed", False) else None
            ),
        )
        window.add_action(action)
    for name, keys, _description in SHORTCUTS:
        window.get_application().set_accels_for_action("win." + name, list(keys))


def quit_application(window) -> None:
    # Always use the normal close handler before quitting the application.
    application = window.get_application()
    window.close()
    if application is not None:
        application.quit()


def open_pages_menu(window) -> None:
    if getattr(window, "_closed", False):
        return
    button = getattr(window, "pages_menu_button", None)
    if button is not None:
        button.popup()


def focus_search(window) -> None:
    if getattr(window, "_closed", False) or not hasattr(window, "_pages"):
        return
    route = window.view_stack.get_visible_child_name()
    if route not in {"search", "library"}:
        if "search" not in window._pages:
            return
        window.navigate("search")
        route = "search"
    controls = getattr(window, route + "_controls", None)
    if controls is not None:
        controls.entry.grab_focus()


def show_settings(window) -> None:
    if getattr(window, "_closed", False):
        return
    if "settings" in getattr(window, "_pages", {}):
        window.navigate("settings")
    else:
        window._open_prelogin_diagnostics()


def cycle_page(window, direction: int) -> None:
    if getattr(window, "_closed", False):
        return
    routes = list(getattr(window, "_pages", {}))
    if routes:
        window.navigate(
            adjacent_route(routes, window.view_stack.get_visible_child_name(), direction)
        )


def show_shortcuts(window, *, Adw) -> None:
    if getattr(window, "_closed", False):
        return
    dialog = Adw.MessageDialog.new(window, "Raccourcis clavier", shortcuts_text())
    dialog.add_response("close", "Fermer")
    dialog.present()
