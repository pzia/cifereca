"""Headless notice-group projection shared by Search and Library (#1129).

Reuse the existing EPUB/Core notice_rows projector, never inspect the book
or its cover again. Widget construction and descriptions stay on the host.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


ESSENTIAL_NOTICE_LABELS = frozenset({
    "Source", "Provenance", "Format", "Langue", "Langues", "Disponibilité",
})
COVER_STATUS_LABELS = {
    "extracted": "Extraite du fichier",
    "no_usable_image": "Aucune image exploitable",
    "metadata_unreadable": "Métadonnées illisibles",
}
COVER_METHOD_LABELS = {
    "declared": "Déclaration EPUB",
    "declared_page": "Page de couverture déclarée",
    "guide": "Guide de couverture",
    "first_spine": "Image unique de la première page (déduction)",
}


def book_cover_notice_rows(metadata):
    """Return local inspection facts only when the EPUB was inspected."""
    if not metadata.get("epub_metadata_schema"):
        return []
    cover = metadata.get("epub_cover")
    status = metadata.get("epub_cover_status")
    status = status if isinstance(status, str) else "not_inspected"
    rows = [("Couverture", COVER_STATUS_LABELS.get(status, "À réinspecter"))]
    method = cover.get("method") if isinstance(cover, Mapping) else None
    if isinstance(method, str) and method:
        rows.append(("Détection", COVER_METHOD_LABELS.get(method, method)))
    return rows


def project_book_notice_groups(
    item: dict[str, Any], *, notice_rows: Callable[[dict], list]
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Return ordered, already-attested notice rows without essential duplicates."""
    metadata = item.get("metadata") or {}
    groups: list[tuple[str, list[tuple[str, str]]]] = []

    rows = [row for row in notice_rows(metadata)
            if row[0] not in ESSENTIAL_NOTICE_LABELS]
    if rows:
        groups.append(("Notice EPUB" if metadata.get("epub_metadata") else "Notice", rows))

    catalogue = metadata.get("catalogue_metadata") or {}
    if catalogue:
        catalogue_rows = [row for row in notice_rows(catalogue)
                          if row[0] not in ESSENTIAL_NOTICE_LABELS]
        if catalogue_rows:
            groups.append(("Catalogue", catalogue_rows))

    facts = book_cover_notice_rows(metadata)
    if facts:
        groups.append(("Image du livre", facts))

    return groups


def add_book_notice(
    window, detail, item, *, notice_rows, include_description=True
) -> None:
    """Preserve summary-before-groups and allow the common detail to own it."""
    if getattr(window, "_closed", False):
        return
    if include_description:
        detail._show_description(window._book_description(item))
    for title, rows in project_book_notice_groups(item, notice_rows=notice_rows):
        detail.add_group(title, rows)
