"""Progressive, account-local tools on the Synchronisation page."""
from __future__ import annotations

from gi.repository import Gtk

from .sync_action_layout import add_sync_action, sync_action_row


def add_sync_detail(group, row, *, action=None, source_row=None):
    """Keep status and the primary action outside an initially closed section.

    Building or opening this section only changes widget layout. The original
    buttons retain their callbacks, sensitivity, consent and account guards.
    Numilog's durable backup status is deliberately not added here.
    """
    expander = getattr(group, "_cifereca_sync_tools", None)
    if expander is None:
        expander = Gtk.Expander(label="Outils et planification")
        state = group._cifereca_sync_tools_state
        source_id = group._cifereca_sync_source_id
        expander.set_expanded(state.get(source_id) is True)
        expander.connect(
            "notify::expanded",
            lambda widget, _param: state.__setitem__(source_id, widget.get_expanded()),
        )
        expander.set_margin_start(12)
        expander.set_margin_end(12)
        expander.set_margin_bottom(12)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        expander.set_child(content)
        group.add(expander)
        group._cifereca_sync_tools = expander
    if action is not None:
        # The action already belongs to the source FlowBox. Remove its
        # FlowBoxChild before reusing the exact same button in its tool row.
        flow = source_row._cifereca_action_flow
        child = action.get_parent()
        child.set_child(None)
        flow.remove(child)
        add_sync_action(row, action)
    expander.get_child().append(sync_action_row(row))
