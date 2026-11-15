"""GTK-free projection of recent local acquisitions (#1129).

Keep the historical row order and key fallback: UI selection and identity
lifetime remain owned by MainWindow. This module neither queries providers
nor reads a file or cache.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .recent_time import format_recent_timestamp
from ..core.local_cover_evidence import VerifiedLocalCoverEvidence


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
    seen_cells: dict[str, Any | None] = {}
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
        row = row_factory(
            key,
            str(item.get("title") or "Sans titre"),
            str(item.get("author") or ""),
            temporal,
            label,
            item.get("thumbnail_url"),
        )
        # Two descriptive rows with the same GTK key are not an exact
        # document binding: revoke any earlier packet, retain both rows.
        # This is visual only and does not rewrite the Core snapshot.
        if key in seen_cells:
            earlier = seen_cells[key]
            if earlier is not None:
                for field in ("_cover_local_evidence", "_cover_document_ref"):
                    if hasattr(earlier, field):
                        delattr(earlier, field)
                if hasattr(earlier, "_public_row_content"):
                    delattr(earlier, "_public_row_content")
            seen_cells[key] = None
        else:
            seen_cells[key] = row
            facts = item.get("_exact_local_cover_evidence")
            if (hasattr(row, "__dict__")
                    and type(facts) is VerifiedLocalCoverEvidence
                    and facts.scope.identity_id == item.get("identity_id")
                    # The Recent DISPLAY key is Placement; N2 uses the
                    # identical Library Artifact+Placement documentary ref.
                    # A matching SHA alone is never enough to bind a row.
                    and facts.scope.source_key == (
                        "artifact:sha256:" + facts.artifact_sha256
                    )
                    and facts.scope.artifact_id == (
                        "sha256:" + facts.artifact_sha256
                    )
                    and facts.scope.artifact_id == item.get("artifact_id")
                    and facts.scope.placement_id == item.get("placement_id")
                    and item.get("key") == key):
                row._cover_local_evidence = facts
                row._cover_document_ref = facts.scope
                row._public_row_content = (
                    "recent", key, item.get("title"), item.get("author"),
                    temporal, label, item.get("thumbnail_url"), facts,
                )
        rows.append(row)
    return rows, objects
