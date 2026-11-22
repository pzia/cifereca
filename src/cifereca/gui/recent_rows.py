"""GTK-free projection of recent local acquisitions (#1129).

Keep the historical row order and key fallback: UI selection and identity
lifetime remain owned by MainWindow. This module neither queries providers
nor reads a file or cache.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from .recent_time import format_recent_timestamp
from ..core.local_cover_evidence import VerifiedLocalCoverEvidence
from ..core.notice_c_local_origin_projection import valid_local_public_c_link


def recent_provider_label(provider: str, provider_display_name=None) -> str:
    """One bounded presentation label for Recent rows and their detail."""
    if provider == "local":
        return "Local"
    if not callable(provider_display_name):
        # Preserve headless/legacy hosts, with the same text boundary.
        candidate = provider.capitalize()
    else:
        try:
            candidate = provider_display_name(provider)
        except Exception:
            return "Source"
    return (
        candidate if type(candidate) is str
        and 0 < len(candidate) <= 80
        and candidate == candidate.strip()
        and all(character.isprintable() for character in candidate)
        else "Source"
    )


def project_recent_rows(
    items: Iterable[dict[str, Any]], *,
    row_factory: Callable[..., Any],
    provider_display_name: Callable[[str], str] | None = None,
) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """Return display rows and the keyed items, without mutating input data.

    Duplicate placement IDs intentionally retain both display rows while the
    lookup mapping points to the last matching item, as the previous view did.
    """
    # Inspect keys before materializing cells: two rows with one GTK key
    # cannot each claim a unique Artifact/Placement. Avoid displaying either
    # the attested image OR a fallback URL for such an ambiguous binding.
    # This consumes any input iterable exactly once; it does not mutate DTOs.
    source_items = list(items)
    keys = [
        str(item.get("placement_id") or f"recent:{index}")
        for index, item in enumerate(source_items)
    ]
    occurrences = Counter(keys)
    rows: list[Any] = []
    objects: dict[str, dict[str, Any]] = {}
    for item, key in zip(source_items, keys):
        objects[key] = item
        provider = str(item.get("provider_id") or "local")
        temporal = format_recent_timestamp(item.get("acquired_at"))
        label = recent_provider_label(provider, provider_display_name)
        packet = item.get("_exact_local_cover_evidence")
        link = item.get("_public_canonical_cover_link")
        safe_link = (
            packet is None and occurrences[key] == 1
            and valid_local_public_c_link(
                link, identity_id=item.get("identity_id"),
                artifact_id=item.get("artifact_id"),
                placement_id=item.get("placement_id"),
            )
            and item.get("_library_view_owner") in (
                None, item.get("identity_id"),
            )
        )
        # If a Core packet exists, only its exact N2 DocumentRef may supply
        # the cover. A rejected/foreign packet or duplicate key must NEVER
        # downgrade silently to the descriptive legacy thumbnail URL.
        legacy_url = (
            item.get("thumbnail_url")
            if packet is None and link is None and occurrences[key] == 1
            else None
        )
        row = row_factory(
            key,
            str(item.get("title") or "Sans titre"),
            str(item.get("author") or ""),
            temporal,
            label,
            legacy_url,
        )
        if hasattr(row, "__dict__"):
            # Selection authority is this exact worker occurrence, not
            # the placement's display key or a matching thumbnail.
            row._recent_source_item = item
        if occurrences[key] > 1 and hasattr(row, "__dict__"):
            # A selection of either duplicate must not open the other row's
            # detail (including a wrongly attributed physical cover).
            row._recent_ambiguous_key = True
        if occurrences[key] == 1:
            facts = packet
            if (hasattr(row, "__dict__")
                    and type(facts) is VerifiedLocalCoverEvidence
                    and facts.scope.identity_id == item.get("identity_id")
                    # Recent GTK key is Placement; N2 uses the same exact
                    # Artifact+Placement source as Library and its detail.
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
                    temporal, label, legacy_url, facts,
                )
            elif hasattr(row, "__dict__") and safe_link:
                row._cover_public_c_link = link
                row._cover_document_ref = link.document
                row._public_row_content = (
                    "recent", key, item.get("title"), item.get("author"),
                    temporal, label, None, link.document, link.epoch,
                )
        rows.append(row)
    return rows, objects
