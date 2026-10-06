from __future__ import annotations


def identity_change_required(current_identity_id: str | None, requested_identity_id: str) -> bool:
    """Return True only when a GTK identity notification represents a real change."""
    return current_identity_id != requested_identity_id
