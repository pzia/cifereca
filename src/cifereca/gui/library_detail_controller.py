"""Library book selection and detail presentation, GTK-independent (#1129).

Owns orchestration only; all widgets, identity-aware services, public document
links and actions remain with MainWindow and are passed via its stable facade.
"""
from __future__ import annotations

from dataclasses import replace
from ..core.notice_c_local_origin_projection import valid_local_public_c_link
from ..core.notice_c_purchase_library_projection import purchase_library_notice, purchase_library_status, purchase_status_label

from .local_book_detail_sections import local_book_detail_sections
from .detail_selection_perf import current_detail_trace
from .local_detail_cover import exact_local_detail_cover


def library_selected(window, row, *, Gtk, unique_library_loan, book_detail_presentation, detail_language, document_links) -> None:
    # Selection signals can arrive while widgets are being disposed.
    if getattr(window, "_closed", False):
        return
    from cifereca.gui.local_public_c_detail_guard import (
        begin_local_public_web_selection, request_local_public_web_action,
    )
    web_token = begin_local_public_web_selection(window, "library")
    window._cancel_cached_book_details()
    if not row:
        window.library_page.detail.empty(
            "Sélectionnez un livre",
            "Sélectionnez un livre dans la liste pour afficher ses détails.",
        )
        return
    if (getattr(row, "_library_ambiguous_key", False) is True
            or getattr(row, "key", None) in getattr(
                window, "_library_ambiguous_keys", (),
            )):
        # The display key is also the dictionary key for action/detail
        # lookup. Reject even a stale once-unique GTK row after a new
        # duplicate-key inventory; a cell rebind is not a permission proof.
        window.library_page.detail.empty(
            "Référence ambiguë",
            "Actualisez la bibliothèque avant d'ouvrir ce détail.",
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
    view_owner = item.get("_library_view_owner")
    # The global-view marker is presentation metadata, not an ACL. A missing
    # or altered marker must never reclassify A's book as usable under active
    # profile B; an inconsistent marker also invalidates private actions.
    # Validate native identity types before equality: untrusted markers may
    # carry hostile __eq__ implementations.
    owner_is_active = (
        type(active) is str and bool(active)
        and type(owner) is str and bool(owner) and owner == active
    )
    foreign = (
        # An explicitly supplied owner must be native and match the active
        # identity. In particular None/empty/non-native is not an owner,
        # even when the global-view presentation marker is absent.
        # Preserve the older unqualified display-row contract only when
        # the owner field itself is absent; downstream local/provider
        # admission remains independent of this presentation guard.
        ("identity_id" in item and not owner_is_active)
        or (view_owner is not None and (
            type(view_owner) is not str or not view_owner
            or not owner_is_active or view_owner != owner
        ))
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
    documentary = purchase_library_notice(item) if not foreign else None
    if documentary != getattr(row, "_purchase_library_notice", None):
        documentary = None
    status = purchase_library_status(item) if not foreign else None
    if status != getattr(row, "_purchase_library_status", None):
        status = None
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
            purchase_notice=documentary,
        ) + ([('Enrichissement documentaire', [('État', purchase_status_label(status))])] if status is not None else []),
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
    web_source = None
    if cover_evidence is None:
        link = item.get("_public_canonical_cover_link")
        pinned_c = (
            not foreign
            and item.get("_exact_local_cover_evidence") is None
            and valid_local_public_c_link(
                link, identity_id=active,
                artifact_id=item.get("artifact_id"),
                placement_id=item.get("placement_id"),
            )
        )
        if pinned_c:
            # Only the actual current GTK list selection may use this
            # Artifact/Placement C after an N3 read. A stale once-unique
            # key cannot open the newer mapped book or paint its cover.
            from .local_public_c_detail_guard import (
                local_public_c_detail_check, staged_local_public_c_detail,
            )
            check = local_public_c_detail_check(
                window, row, item, link, view="library",
            )
            if (getattr(row, "_cover_public_c_link", None) is not None
                    and not check()):
                detail.empty(
                    "Référence périmée",
                    "Actualisez la bibliothèque avant d'ouvrir ce détail.",
                )
                return
            presentation = replace(
                presentation, cover_document_ref=link.document,
                thumbnail_url=None,
            )
            with staged_local_public_c_detail(
                detail, check, minimum_revision=link.epoch,
            ):
                detail.show_presentation(
                    presentation, description=description,
                )
            web_source = (link, check)
        else:
            detail.show_presentation(
                presentation, description=description,
            )
    else:
        detail.show_local_presentation(
            presentation, description=description, cover_evidence=cover_evidence,
        )
    window._add_local_book_actions(detail,item)
    if not foreign:
        from .local_origin_suggestion_action import add_local_origin_suggestion_action
        add_local_origin_suggestion_action(window, detail, row, item, token=web_token)
    if not foreign:
        # Preserve original action ordering for current-profile details.
        window._add_book_recommendation_action(detail, item)
    window._add_facets(window.library_page.detail, item, window.library_controls)
    if not foreign:
        # Descriptive foreign metadata must not trigger provider-bound
        # details or plugin actions with B's selected credentials.
        window._add_plugin_book_actions(detail, item)
        window._request_cached_book_details("library", row.key, item)
    if web_source is not None:
        link, check = web_source
        request_local_public_web_action(
            window, detail, row, link, view="library",
            token=web_token, source_current=check,
        )
