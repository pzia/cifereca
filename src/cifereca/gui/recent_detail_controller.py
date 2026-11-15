"""Recent acquisition detail presenter, using injected window/formatting (#1129)."""
from __future__ import annotations

from ..core.local_cover_evidence import VerifiedLocalCoverEvidence
from .detail_model import book_detail_presentation
from .local_detail_cover import exact_local_detail_cover


def recent_selected(window, row, *, Path, format_recent_timestamp) -> None:
    if getattr(window, "_closed", False) or not row:
        return
    item = window._recent_objects.get(row.key)
    if not item:
        return
    detail = window.recent_page.detail
    active = getattr(
        getattr(getattr(window, "service", None), "current_identity", None),
        "identity_id", None,
    )
    # An A->B profile switch may retain a previously mapped Recent row.
    # Never render A's physical/borrower cover in B's detail pane.
    owner = item.get("identity_id")
    if (type(active) is str and active
            and type(owner) is str and owner and owner != active):
        detail.empty("Profil modifié", "Rouvrez les livres récents pour ce profil.")
        return
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
    facts = exact_local_detail_cover(item, active_identity_id=active)
    details = dict(
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
    if facts is not None:
        # The same owner-proved Core packet was attached to the recent list
        # row. Detail reuses N2, not thumbnail_url or a physical N3 join.
        detail.show_local_presentation(
            book_detail_presentation(**details), cover_evidence=facts,
        )
    else:
        # An explicit but malformed/stale private proof is not permission
        # to bypass N1's embedded priority via a warm origin/catalog URL.
        if type(item.get("_exact_local_cover_evidence")) is VerifiedLocalCoverEvidence:
            details["thumbnail"] = None
        detail.show_summary(**details)
    window._add_book_notice(detail,item)
    window._add_local_book_actions(detail,item)
    window._add_facets(detail, item, window.search_controls)
