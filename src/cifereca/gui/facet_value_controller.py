"""CoreUI P0 #1129: one native affordance for clickable book facets.

Only UI composition and explicit navigation/refresh are moved here. The
existing query model remains the sole facet evaluator; the caller's controls
and MainWindow remain owners. No GTK import, service lookup or provider I/O.
"""
from __future__ import annotations

from .query_model import apply_facet


def search_facet(window, field, text, controls) -> None:
    """Apply a facet silently, then refresh exactly its owning context."""
    if getattr(window, "_closed", False):
        return
    state = controls.state()
    updated = apply_facet(state, field, text)
    # Explicit actions must refresh even when the selected facet was already
    # active; notification-based refresh would otherwise run twice.
    library_controls = getattr(window, "library_controls", None)
    target_controls = (
        library_controls if controls is library_controls else window.search_controls
    )
    target_controls.set_state(updated, notify=False)
    if target_controls is library_controls:
        window.navigate("library")
        window._render_library()
        return
    window.navigate("search")
    window.run_search(False)


def facet_value_button(window, label, field, value, controls, *, Gtk, Pango):
    """The visible value is itself the keyboard-accessible filter target."""
    caption = str(label or value or "").strip()
    button = Gtk.Button()
    button.add_css_class("flat")
    button.add_css_class("facet-value")
    text = Gtk.Label(label=caption, xalign=0)
    text.set_ellipsize(Pango.EllipsizeMode.END)
    text.set_max_width_chars(28)
    button.set_child(text)
    tooltip = f"Filtrer par {caption}"
    button.set_tooltip_text(tooltip)
    try:
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    except (AttributeError, TypeError):
        pass
    button._cifereca_facet_field = str(field)
    button._cifereca_facet_value = value
    button.connect(
        "clicked",
        lambda _button, f=field, v=value: (
            window._search_facet(f, v, controls)
            if not getattr(window, "_closed", False) else None
        ),
    )
    return button


def facet_value_group(window, choices, field, controls, *, Gtk):
    """Bounded, ordered and deduplicated facet buttons in one FlowBox."""
    group = Gtk.FlowBox()
    group.set_selection_mode(Gtk.SelectionMode.NONE)
    group.set_halign(Gtk.Align.START)
    group.set_valign(Gtk.Align.CENTER)
    group.set_min_children_per_line(1)
    group.set_max_children_per_line(4)
    group.set_column_spacing(4)
    group.set_row_spacing(4)
    seen = set()
    for label, value in choices:
        key = (str(label or "").strip().casefold(), str(value or ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        group.insert(window._facet_value_button(label, field, value, controls), -1)
    return group
