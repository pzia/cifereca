"""GTK-independent timer cleanup policy for MainWindow (#1132)."""

from __future__ import annotations

from typing import Callable


def cancel_source(source_id: int | None, remove: Callable[[int], object]) -> None:
    """Best-effort cancellation; callbacks may already have completed."""
    if source_id is None:
        return
    try:
        remove(source_id)
    except Exception:
        pass
