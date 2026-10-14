"""Headless policy for collapsing completed-task feedback (#1132)."""

from __future__ import annotations


def can_reduce_activity(*, closed: bool, active_count: int, pending_error: bool, has_view: bool) -> bool:
    """Never dismiss a live operation, unacknowledged failure or closed view."""
    return not closed and active_count == 0 and not pending_error and has_view
