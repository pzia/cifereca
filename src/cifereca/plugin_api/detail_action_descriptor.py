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
