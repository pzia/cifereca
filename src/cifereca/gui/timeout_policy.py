"""Pure timeout normalization for GUI feedback (#1132)."""

from __future__ import annotations


def bounded_seconds(value: object, *, maximum: int = 10, default: int = 3) -> int:
    """Accept only ordinary integer durations, never unbounded timers."""
    if type(value) is not int:
        return default
    return max(1, min(value, maximum))
