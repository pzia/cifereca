"""Presentation-only action state shared by plugin and cached book details."""
from __future__ import annotations

from ..plugin_api.detail_action_descriptor import public_action_reason


def apply_detail_action_state(button, action) -> None:
    """Reflect the producer's state without evaluating permissions or doing IO.

    Unknown states fail closed. Keep the original button/callback so the
    adaptive bar can move it to overflow without losing its state or reason.
    The marker also guards queued/synthetic signals on insensitive buttons.
    """
    raw_state = getattr(action, "state", "available")
    # Do not compare arbitrary objects supplied by a plugin with state labels.
    state = raw_state if type(raw_state) is str else None
    uri = getattr(action, "uri", "")
    executable = callable(getattr(action, "callback", None)) or (
        type(uri) is str and bool(uri.strip())
    )
    # A renderer cannot execute a missing target. This is a structural
    # check, not an admission decision; URL validation stays in the adapters.
    available = state == "available" and executable
    button._cifereca_action_state_blocked = not available
    button.set_sensitive(available)
    priority = getattr(action, "priority", None)
    if type(priority) is int:
        button._cifereca_action_priority = priority
    destructive = getattr(action, "destructive", False) is True
    button._cifereca_action_destructive = destructive
    styler = getattr(button, "add_css_class" if destructive else "remove_css_class", None)
    if callable(styler):
        styler("destructive-action")
    raw_reason = getattr(action, "unavailable_reason", "")
    reason = public_action_reason(raw_reason)
    if state == "available" and not executable:
        reason = "Aucune action exécutable"
    if available:
        tooltip = str(action.title)
    else:
        status = "En cours" if state == "busy" else "Indisponible"
        tooltip = f"{action.title} · {status}"
        if reason:
            tooltip += f" · {reason}"
    button.set_tooltip_text(tooltip)
