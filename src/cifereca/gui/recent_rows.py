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
        rows.append(row_factory(
            key,
            str(item.get("title") or "Sans titre"),
            str(item.get("author") or ""),
            temporal,
            "Kobo" if provider == "kobo" else provider.capitalize(),
            item.get("thumbnail_url"),
        ))
    return rows, objects
