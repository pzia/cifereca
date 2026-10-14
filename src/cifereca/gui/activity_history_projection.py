"""GTK-free projection of task history rows (#1132)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def activity_history_rows(entries: Iterable[Any]) -> tuple[tuple[str, bool], ...]:
    """Keep ordered display text and error styling independent of GTK widgets."""
    return tuple((entry.display_text, entry.state == "error") for entry in entries)
