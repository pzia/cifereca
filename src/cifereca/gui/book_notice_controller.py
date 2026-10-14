"""Headless notice-group projection shared by Search and Library (#1129).

Reuse the existing EPUB/Core notice_rows projector, never inspect the book
or its cover again. Widget construction and descriptions stay on the host.
"""
from __future__ import annotations

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

    # Never infer a cover from an uninspected EPUB.
    if metadata.get("epub_metadata_schema"):
        cover = metadata.get("epub_cover") or {}
        status = metadata.get("epub_cover_status") or "not_inspected"
        facts = [("Couverture", COVER_STATUS_LABELS.get(status, "À réinspecter"))]
        if cover.get("method"):
            facts.append(("Détection", COVER_METHOD_LABELS.get(
                cover["method"], cover["method"],
            )))
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
