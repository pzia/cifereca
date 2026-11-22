"""Recent acquisition detail presenter, using injected window/formatting (#1129)."""
from __future__ import annotations

from dataclasses import replace
from ..core.notice_c_local_origin_projection import valid_local_public_c_link

from .local_detail_cover import exact_recent_detail_cover
from .detail_model import book_detail_presentation
from .recent_rows import recent_provider_label


def recent_selected(window, row, *, Path, format_recent_timestamp) -> None:
    if getattr(window, "_closed", False):
        return
    from cifereca.gui.local_public_c_detail_guard import (
        begin_local_public_web_selection, request_local_public_web_action,
    )
    web_token = begin_local_public_web_selection(window, "recent")
    if not row:
        return
    if (getattr(row, "_recent_ambiguous_key", False) is True
            or row.key in getattr(window, "_recent_ambiguous_keys", ())):
        # The lookup intentionally keeps the last row for compatibility,
        # but a duplicate visual key cannot select an exact book/detail.
        window.recent_page.detail.empty(
            "Référence ambiguë",
            "Actualisez les livres récents avant d'ouvrir ce détail.",
        )
        return
    item = window._recent_objects.get(row.key)
    if not item:
        return
    detail = window.recent_page.detail
    if (hasattr(row, "_recent_source_item")
            and row._recent_source_item is not item):
        detail.empty(
            "Référence périmée",
            "Actualisez les livres récents avant d'ouvrir ce détail.",
        )
        return
    active = getattr(
        getattr(getattr(window, "service", None), "current_identity", None),
        "identity_id", None,
    )
    owner = item.get("identity_id")
    if (type(owner) is str and owner
            and type(active) is str and active and owner != active):
        # A stale mapped A row must not expose its title/path to B.
        detail.empty("Profil modifié", "Rouvrez les livres récents pour ce profil.")
        return
    if (item.get("_exact_local_cover_evidence") is not None
            and (type(active) is not str or not active or owner != active)):
        detail.empty("Profil modifié", "Rouvrez les livres récents pour ce profil.")
        return
    provider = str(item.get("provider_id") or "local")
    # The recovery section uses exactly the same bounded source label as
    # the list, including when a descriptor is absent or malformed.
    lookup = getattr(getattr(window, "service", None), "provider_display_name", None)
    provenance = recent_provider_label(provider, lookup)
    full_path = str(item.get("path") or "")
    file_name = Path(full_path).name if full_path else "—"
    acquired = format_recent_timestamp(item.get("acquired_at"), detailed=True)
    evidence = exact_recent_detail_cover(
        item, active_identity_id=active,
    )
    summary = dict(
        title=str(item.get("title") or "Sans titre"),
        subtitle=str(item.get("author") or ""),
        thumbnail=(item.get("thumbnail_url")
                   if item.get("_public_canonical_cover_link") is None
                   else None),
        badges=[provenance, "Local"],
        group_title="Récupération",
        rows=[
            ("Fichier", file_name),
            ("Récupéré", acquired),
            ("Provenance", provenance),
            ("Chemin", full_path or "—"),
        ],
    )
    def presentation():
        # Use the same bounded bibliographic projection as Library. The
        # recovery section and source-scoped cover remain independent.
        return book_detail_presentation(**summary, metadata=item.get("metadata"))

    if evidence is None:
        link = item.get("_public_canonical_cover_link")
        if (item.get("_exact_local_cover_evidence") is None
                and valid_local_public_c_link(
                    link, identity_id=active,
                    artifact_id=item.get("artifact_id"),
                    placement_id=item.get("placement_id"),
                )):
            from .local_public_c_detail_guard import (
                local_public_c_detail_check, staged_local_public_c_detail,
            )
            check = local_public_c_detail_check(
                window, row, item, link, view="recent",
            )
            # A stale GTK selection with the same placement ID may not
            # open a different item's C or private local actions.
            if (getattr(row, "_cover_public_c_link", None) is not None
                    and not check()):
                detail.empty(
                    "Référence périmée",
                    "Actualisez les livres récents avant d'ouvrir ce détail.",
                )
                return
            public_presentation = replace(
                presentation(),
                cover_document_ref=link.document,
                thumbnail_url=None,
            )
            with staged_local_public_c_detail(
                detail, check, minimum_revision=link.epoch,
            ):
                detail.show_presentation(public_presentation)
            window._add_book_notice(detail, item)
            window._add_local_book_actions(detail, item)
            window._add_facets(detail, item, window.search_controls)
            request_local_public_web_action(
                window, detail, row, link, view="recent",
                token=web_token, source_current=check,
            )
            return
        # A stale/foreign private proof does NOT authorize a legacy origin
        # URL. Only rows without any Core packet keep their old URL path.
        if item.get("_exact_local_cover_evidence") is not None:
            summary["thumbnail"] = None
        render = getattr(detail, "show_presentation", None)
        if callable(render):
            render(presentation())
        else:
            detail.show_summary(**summary)
    else:
        render = getattr(detail, "show_local_presentation", None)
        if callable(render):
            # One N2 DocumentRef for both list and detail. Do not issue a
            # legacy URL request before this attested EPUB source is chosen.
            render(
                presentation(),
                cover_evidence=evidence,
            )
        else:
            # A custom/old host cannot use the attested EPUB source; it
            # must not downgrade to a warm URL by accident.
            summary["thumbnail"] = None
            render = getattr(detail, "show_presentation", None)
            if callable(render):
                render(presentation())
            else:
                detail.show_summary(**summary)
    window._add_book_notice(detail,item)
    window._add_local_book_actions(detail,item)
    window._add_facets(detail, item, window.search_controls)
