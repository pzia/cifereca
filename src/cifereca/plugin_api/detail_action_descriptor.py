"""Structural predicate for a passive cache action, never execution authority."""
from .models import UiBookDetailAction


def inactive_without_target(action: object) -> bool:
    return (
        type(action) is UiBookDetailAction
        and type(action.state) is str
        and action.state in ("unavailable", "busy")
        and type(action.uri) is str and action.uri == ""
        and action.callback is None
    )


def public_action_reason(value: object) -> str:
    """Normalize a passive reason identically on direct and cached paths.

    Never invoke plugin coercion hooks. Bound work and output, reject control
    characters after whitespace normalization, and leave capability unchanged.
    """
    if type(value) is not str:
        return ""
    reason = " ".join(value[:1024].split())[:280]
    return reason if reason.isprintable() else ""
