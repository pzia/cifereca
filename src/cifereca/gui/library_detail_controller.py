"""Library book selection and detail presentation, GTK-independent (#1129).

Owns orchestration only; all widgets, identity-aware services, public document
links and actions remain with MainWindow and are passed via its stable facade.
"""
from __future__ import annotations

from dataclasses import replace
from ..core.notice_c_local_origin_projection import valid_local_public_c_link

from .local_book_detail_sections import local_book_detail_sections
from .detail_selection_perf import current_detail_trace
from .local_detail_cover import exact_local_detail_cover


def library_selected(window, row, *, Gtk, unique_library_loan, book_detail_presentation, detail_language, document_links) -> None:
    # Selection signals can arrive while widgets are being disposed.
    if getattr(window, "_closed", False):
        return
    window._cancel_cached_book_details()
    if not row:
        window.library_page.detail.empty(
            "Sélectionnez un livre",
            "Sélectionnez un livre dans la liste pour afficher ses détails.",
        )
        return
    item = window._library_objects.get(row.key)
    if not item:
        return
    # Global Library is a read scope, not permission to use A's provider
    # account under active B. Only the separate local-send Core admission
    # may offer a consented copy of an exact physical foreign placement.
    active = getattr(
        getattr(window.service, "current_identity", None), "identity_id", None,
    )
    owner = item.get("identity_id")
    foreign = (
        item.get("_library_view_owner") == owner
        and type(owner) is str and bool(owner) and owner != active
    )
    if not foreign and item.get("source") == "loan" and item.get("loan_id"):
        # A merged catalogue notice may not identify the exact borrower.
        # In that case never expose an action for an arbitrary loan source.
        loan = unique_library_loan(window.loan_objects.values(), item)
        if loan:
            window.library_page.detail.show_loan(loan, include_pnb_action=False)
            actions = []
            if window.service.can_download_loan(loan):
                prepare = Gtk.Button(label="Télécharger le livre du prêt"); prepare.add_css_class("suggested-action")
                prepare.set_tooltip_text("Récupère le prêt et traite son ACSM avec Kobo-Adept. Un fichier déjà présent est conservé ; une nouvelle copie peut être créée. Aucun nouvel emprunt.")
                prepare.connect("clicked", lambda _b: window.prepare_pnb_for_loan(loan, False, prepare)); actions.append(prepare)
            window.library_page.detail.add_actions(actions)
            window._add_facets(window.library_page.detail, item, window.library_controls)
            window._add_local_book_actions(window.library_page.detail, item)
            window._add_book_recommendation_action(window.library_page.detail, item)
            window._request_cached_book_details("library", row.key, item)
            return

    detail = window.library_page.detail
    description = window._book_description(item)
    trace = current_detail_trace()
    projection_started = trace.clock() if trace else None
    presentation = book_detail_presentation(
        title=str(item.get("title") or "Sans titre"),
        subtitle=str(item.get("author") or ""),
        # When a Core packet is present, only its exact N2 association can
        # authorize painting. If rejected (owner/Artifact/Placement), do
        # NOT substitute an unqualified cached catalogue/legacy URL.
        thumbnail=(item.get("thumbnail_url")
                   if item.get("_exact_local_cover_evidence") is None
                   and item.get("_public_canonical_cover_link") is None
                   else None),
        badges=[],
        section_rows=local_book_detail_sections(
            item,
            provider_display_name=window.service.provider_display_name,
            availability_label=window._library_availability_label,
            identity_by_id=window.service.identity_by_id,
            sync_status_label=window._kobo_backup_status_label,
            language_label=detail_language,
        ),
        links=() if foreign else document_links(item),
        metadata=item.get('metadata'),
    )
    if trace is not None:
        trace.record("projection", projection_started)
    # The owner's exact Core Artifact+Placement proof is the ONLY way to
    # adopt the shared DocumentRef-first cover path. A global foreign
    # bibliography never inherits A's private EPUB/source association.
    cover_evidence = (
        exact_local_detail_cover(item, active_identity_id=active)
        if not foreign else None
    )
    if cover_evidence is None:
        link = item.get("_public_canonical_cover_link")
        if (not foreign
                and item.get("_exact_local_cover_evidence") is None
                and valid_local_public_c_link(
                    link, identity_id=active,
                    artifact_id=item.get("artifact_id"),
                    placement_id=item.get("placement_id"),
                )):
            presentation = replace(
                presentation, cover_document_ref=link.document,
                thumbnail_url=None,
            )
        detail.show_presentation(presentation, description=description)
    else:
        detail.show_local_presentation(
            presentation, description=description, cover_evidence=cover_evidence,
        )
    window._add_local_book_actions(detail,item)
    if not foreign:
        # Preserve original action ordering for current-profile details.
        window._add_book_recommendation_action(detail, item)
    window._add_facets(window.library_page.detail, item, window.library_controls)
    if not foreign:
        # Descriptive foreign metadata must not trigger provider-bound
        # details or plugin actions with B's selected credentials.
        window._add_plugin_book_actions(detail, item)
        window._request_cached_book_details("library", row.key, item)
