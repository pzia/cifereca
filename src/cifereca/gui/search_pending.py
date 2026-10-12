"""Deduplicate only the current in-flight online search (CoreUI P0 #1128).

An old remote search from the same identity/query may still have unfinished
callbacks after a newer *local* search changed the search generation.
Its pending marker must not suppress a fresh user-requested online search.
"""
from __future__ import annotations

from typing import Any


def pending_online_search_matches(
    pending_key: Any,
    requested_key: Any,
    pending_generation: int | None,
    current_generation: int,
) -> bool:
    """Whether this exact online request is still current and in flight."""
    return (
        pending_key == requested_key
        and type(pending_generation) is int
        and type(current_generation) is int
        and pending_generation == current_generation
    )
