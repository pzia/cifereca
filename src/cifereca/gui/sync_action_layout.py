"""Keep Synchronisation descriptions full-width and wrap their action buttons."""
from __future__ import annotations

from gi.repository import Gtk


def sync_action_row(row):
    """Use uniform containers so PreferencesGroup preserves metadata/action order."""
    container = getattr(row, "_cifereca_action_container", None)
    if container is None:
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.append(row)
        row._cifereca_action_container = container
    return container


def add_sync_action(row, button):
    """Retain the original button/callback while allowing rows at 760px."""
    flow = getattr(row, "_cifereca_action_flow", None)
    if flow is None:
        container = sync_action_row(row)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(False)
        flow.set_min_children_per_line(1)
        flow.set_max_children_per_line(3)
        flow.set_column_spacing(6)
        flow.set_row_spacing(6)
        flow.set_hexpand(True)
        flow.set_margin_start(10)
        flow.set_margin_end(10)
        flow.set_margin_bottom(10)
        # FlowBox's minimum request can otherwise prevent shrinking its
        # toplevel before it gets the new width at which to reflow children.
        viewport = Gtk.ScrolledWindow()
        viewport.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        viewport.set_propagate_natural_width(False)
        viewport.set_min_content_width(0)
        viewport.set_child(flow)
        container.append(viewport)
        row._cifereca_action_flow = flow
    button.set_valign(Gtk.Align.CENTER)
    # #2141: FlowBox may allocate a whole wide cell to a lone action. Keep
    # the interactive button at its natural width (global and per-account),
    # without changing its callback, accessible label or responsive wrapping.
    button.set_halign(Gtk.Align.START)
    button.set_hexpand(False)
    # Large fonts must wrap the caption rather than impose a new minimum
    # window width. Accessible names, icons and tooltip remain unchanged.
    pending = [button]
    while pending:
        widget = pending.pop()
        if isinstance(widget, Gtk.Label):
            widget.set_wrap(True)
            # A one-character minimum was fine while buttons filled their
            # FlowBox cells. With natural-width actions it makes long labels
            # request a narrow, many-line button (>72px high in real GTK).
            # Reserve a two-line-friendly minimum while the horizontal
            # ScrolledWindow continues to allow the 760px viewport to shrink.
            widget.set_width_chars(28)
            widget.set_max_width_chars(44)
        child = widget.get_first_child()
        while child is not None:
            pending.append(child)
            child = child.get_next_sibling()
    flow.insert(button, -1)
    child = button.get_parent()
    if isinstance(child, Gtk.FlowBoxChild):
        child.set_focusable(False)
        child.set_halign(Gtk.Align.START)
        child.set_hexpand(False)
