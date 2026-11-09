"""GTK-free projection of recent local acquisitions (#1129).

Keep the historical row order and key fallback: UI selection and identity
lifetime remain owned by MainWindow. This module neither queries providers
nor reads a file or cache.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .recent_time import format_recent_timestamp


def project_recent_rows(
    items: Iterable[dict[str, Any]], *,
    row_factory: Callable[..., Any],
    provider_display_name: Callable[[str], str] | None = None,
) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """Return display rows and the keyed items, without mutating input data.

    Duplicate placement IDs intentionally retain both display rows while the
    lookup mapping points to the last matching item, as the previous view did.
    """
    rows: list[Any] = []
    objects: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("placement_id") or f"recent:{len(rows)}")
        objects[key] = item
        provider = str(item.get("provider_id") or "local")
        temporal = format_recent_timestamp(item.get("acquired_at"))
        if provider == "local":
            label = "Local"
        elif provider_display_name is None:
            label = provider.capitalize()  # Headless compatibility only.
        else:
            try:
                candidate = provider_display_name(provider)
                label = (
                    candidate if type(candidate) is str
                    and 0 < len(candidate) <= 80
                    and candidate == candidate.strip()
                    and all(character.isprintable() for character in candidate)
                    else "Source"
                )
            except Exception:
                label = "Source"
        rows.append(row_factory(
            key,
            str(item.get("title") or "Sans titre"),
            str(item.get("author") or ""),
            temporal,
            label,
            item.get("thumbnail_url"),
        ))
    return rows, objects
