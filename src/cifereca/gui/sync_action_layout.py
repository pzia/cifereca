"""Keep Synchronisation descriptions full-width and wrap their action buttons."""
from __future__ import annotations

from gi.repository import Gtk


def sync_action_row(row):
    """Return the row with its own action area, or an actionless row unchanged."""
    return getattr(row, "_cifereca_action_container", row)


def add_sync_action(row, button):
    """Retain the original button/callback while allowing rows at 760px."""
    flow = getattr(row, "_cifereca_action_flow", None)
    if flow is None:
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
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
        container.append(row)
        container.append(flow)
        row._cifereca_action_container = container
        row._cifereca_action_flow = flow
    button.set_valign(Gtk.Align.CENTER)
    # Large fonts must wrap the caption rather than impose a new minimum
    # window width. Accessible names, icons and tooltip remain unchanged.
    pending = [button]
    while pending:
        widget = pending.pop()
        if isinstance(widget, Gtk.Label):
            widget.set_wrap(True)
            widget.set_width_chars(1)
            widget.set_max_width_chars(44)
        child = widget.get_first_child()
        while child is not None:
            pending.append(child)
            child = child.get_next_sibling()
    flow.insert(button, -1)
    child = button.get_parent()
    if isinstance(child, Gtk.FlowBoxChild):
        child.set_focusable(False)
