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
        title = str(item.get("title") or "Sans titre")
        author = str(item.get("author") or "")
        image = item.get("thumbnail_url")
        row = row_factory(key, title, author, temporal, label, image)
        # The Recent display key is a Placement ID, NOT the documentary
        # SourceKey. Reuse only the exact Core scope. N3 stays physical and
        # cross-profile shared, without promoting another owner's rights.
        facts = item.get("_exact_local_cover_evidence")
        if (hasattr(row, "__dict__")
                and type(facts) is VerifiedLocalCoverEvidence
                and facts.scope.identity_id == item.get("identity_id")
                and facts.scope.artifact_id == item.get("artifact_id")
                and facts.scope.placement_id == item.get("placement_id")):
            row._cover_local_evidence = facts
            row._cover_document_ref = facts.scope
            # Presentation revision changes when the documentary proof
            # changes, even if the same image URI remains physically hot.
            row._public_row_content = (
                "recent", key, title, author, temporal, label, image, facts,
            )
        rows.append(row)
    return rows, objects
