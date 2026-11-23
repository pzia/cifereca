"""#2385: six-mode catalogue navigator on the existing GTK C read model.

The host supplies a worker-backed load(mode, page) function and must ensure
the returned immutable Core view belongs to the current query/context.
No supplier access, rights operation or implicit profile-specific catalogue.
"""
from __future__ import annotations

from .notice_c_public_catalogue_widget import render_public_catalogue_view

_MODES = (
    ("list", "Liste"), ("grid", "Grille"), ("compact", "Compact"),
    ("detail", "Détail"), ("sources", "Sources"), ("timeline", "Chronologie"),
)


def build_public_catalogue_navigator(*, Gtk, load, initial_mode="list"):
    """Interactive mode and page selection, never a remote provider action."""
    if type(initial_mode) is not str or initial_mode not in dict(_MODES):
        raise ValueError("invalid_public_catalogue_view")
    if not callable(load):
        raise ValueError("invalid_public_catalogue_loader")
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    root.append(controls)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    root.append(content)
    state = {"mode": initial_mode, "page": 0}

    def show():
        view = load(state["mode"], state["page"])
        if view.mode != state["mode"]:
            raise ValueError("stale_public_catalogue_view")
        child = content.get_first_child()
        while child is not None:
            content.remove(child)
            child = content.get_first_child()
        content.append(render_public_catalogue_view(view, Gtk=Gtk))
        if view.has_more:
            more = Gtk.Button(label="Page suivante")
            more.connect("clicked", lambda _button: change_page(1))
            content.append(more)
        if state["page"] > 0:
            back = Gtk.Button(label="Page précédente")
            back.connect("clicked", lambda _button: change_page(-1))
            content.append(back)

    def change_page(delta):
        state["page"] = max(0, state["page"] + delta)
        show()

    def change_mode(mode):
        state["mode"], state["page"] = mode, 0
        show()

    for mode, label in _MODES:
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda _button, selected=mode: change_mode(selected))
        controls.append(button)
    show()
    return root
