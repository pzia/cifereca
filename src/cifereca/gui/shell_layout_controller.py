"""P0 #1130: shell navigation-row and pages-menu layout adapters.

The window retains GTK widgets and route state. The controller owns only
idempotent presentation operations and imports no GTK runtime.
"""
from __future__ import annotations


def add_nav_row(window, route: str, label: str, icon_name: str, *, Gtk) -> None:
    if getattr(window, "_closed", False) or route in window.nav_rows:
        return
    row = Gtk.ListBoxRow()
    row.set_name(route)
    line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    line.set_margin_top(7)
    line.set_margin_bottom(7)
    line.set_margin_start(8)
    line.set_margin_end(8)
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_pixel_size(18)
    line.append(icon)
    text = Gtk.Label(label=label, xalign=0)
    text.set_hexpand(True)
    line.append(text)
    row.set_child(line)
    window.nav_list.append(row)
    window.nav_rows[route] = row


def ensure_pages_menu_rightmost(window) -> None:
    if getattr(window, "_closed", False):
        return
    controls = getattr(window, "_header_controls", None)
    menu = getattr(window, "pages_menu_button", None)
    if controls is None or menu is None:
        return
    children = getattr(controls, "children", None)
    if isinstance(children, list):
        if menu in children:
            children.remove(menu)
        children.append(menu)
        return
    get_last = getattr(controls, "get_last_child", None)
    if callable(get_last) and get_last() is menu:
        return
    get_parent = getattr(menu, "get_parent", None)
    if callable(get_parent) and get_parent() is controls:
        controls.remove(menu)
    controls.append(menu)