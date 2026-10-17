"""Recent acquisition detail presenter, using injected window/formatting (#1129)."""
from __future__ import annotations


def recent_selected(window, row, *, Path, format_recent_timestamp) -> None:
    if getattr(window, "_closed", False) or not row:
        return
    item = window._recent_objects.get(row.key)
    if not item:
        return
    detail = window.recent_page.detail
    provider = str(item.get("provider_id") or "local")
    # Récents has its own "Récupération" section, but should display the
    # *same provider name* as the common Recherche/Bibliothèque detail views.
    # Provider label lookup is presentation-only and must never trigger IO.
    provenance = provider.capitalize()
    lookup = getattr(getattr(window, "service", None), "provider_display_name", None)
    if callable(lookup):
        try:
            candidate = lookup(provider)
        except Exception:
            # Broken/removed provider labels are not allowed to break the
            # details page, nor to surface exceptions carrying private data.
            candidate = None
        if isinstance(candidate, str) and candidate.strip():
            provenance = candidate.strip()
    full_path = str(item.get("path") or "")
    file_name = Path(full_path).name if full_path else "—"
    acquired = format_recent_timestamp(item.get("acquired_at"), detailed=True)
    detail.show_summary(
        title=str(item.get("title") or "Sans titre"),
        subtitle=str(item.get("author") or ""),
        thumbnail=item.get("thumbnail_url"),
        badges=[provenance, "Local"],
        group_title="Récupération",
        rows=[
            ("Fichier", file_name),
            ("Récupéré", acquired),
            ("Provenance", provenance),
            ("Chemin", full_path or "—"),
        ],
    )
    window._add_book_notice(detail,item)
    window._add_local_book_actions(detail,item)
    window._add_facets(detail, item, window.search_controls)
