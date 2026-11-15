"""Recent acquisition detail presenter, using injected window/formatting (#1129)."""
from __future__ import annotations

from ..core.local_cover_evidence import VerifiedLocalCoverEvidence


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
    cover_document = None
    thumbnail = item.get("thumbnail_url")
    facts = item.get("_exact_local_cover_evidence")
    if facts is not None:
        # Most headless and historical Recent rows have no Core cover packet
        # and no connected service. Never touch account context for them.
        owner = getattr(
            getattr(getattr(window, "service", None), "current_identity", None),
            "identity_id", None,
        )
        if (type(facts) is VerifiedLocalCoverEvidence
                and facts.scope.identity_id == owner
                and facts.scope.artifact_id == item.get("artifact_id")
                and facts.scope.placement_id == item.get("placement_id")):
            bridge = getattr(window, "document_covers", None)
            publisher = getattr(window, "local_cover_adapter", None)
            if bridge is not None and publisher is not None:
                try:
                    cover_document = publisher.register(bridge, facts)
                except (TypeError, ValueError, RuntimeError):
                    cover_document = None
        if cover_document is None:
            thumbnail = None
    summary_kwargs = dict(
        title=str(item.get("title") or "Sans titre"),
        subtitle=str(item.get("author") or ""),
        thumbnail=thumbnail,
        badges=[provenance, "Local"],
        group_title="Récupération",
        rows=[
            ("Fichier", file_name),
            ("Récupéré", acquired),
            ("Provenance", provenance),
            ("Chemin", full_path or "—"),
        ],
    )
    if cover_document is not None:
        prepare = getattr(detail, "prepare_document_cover", None)
        if callable(prepare):
            prepare(cover_document)
    detail.show_summary(**summary_kwargs)
    window._add_book_notice(detail,item)
    window._add_local_book_actions(detail,item)
    window._add_facets(detail, item, window.search_controls)
