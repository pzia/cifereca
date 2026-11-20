"""Presentation-only action state shared by plugin and cached book details."""
from __future__ import annotations


def apply_detail_action_state(button, action) -> None:
    """Reflect the producer's state without evaluating permissions or doing IO.

    Unknown states fail closed. Keep the original button/callback so the
    adaptive bar can move it to overflow without losing its state or reason.
    The marker also guards queued/synthetic signals on insensitive buttons.
    """
    state = getattr(action, "state", "available")
    available = type(state) is str and state == "available"
    button._cifereca_action_state_blocked = not available
    button.set_sensitive(available)
    reason = str(getattr(action, "unavailable_reason", "") or "").strip()
    if available:
        tooltip = str(action.title)
    else:
        status = "En cours" if state == "busy" else "Indisponible"
        tooltip = f"{action.title} · {status}"
        if reason:
            tooltip += f" · {reason}"
    button.set_tooltip_text(tooltip)
