"""Library book selection and detail presentation, GTK-independent (#1129).

Owns orchestration only; all widgets, identity-aware services, public document
links and actions remain with MainWindow and are passed via its stable facade.
"""
from __future__ import annotations

from .local_book_detail_sections import local_book_detail_sections


def possession_usage_label(possession, provider_name: str) -> str:
    """Describe one *attested* entitlement, never infer kind from provider.

    Provider ids and catalogue flags cannot establish an owned right or a
    loan. Archived/unknown records do not become active merely because they
    share a title, identity or physical local placement.
    """
    kind = possession.get("kind") if type(possession) is dict else None
    state = possession.get("state") if type(possession) is dict else None
    if kind == "owned" and state == "active":
        return f"Achat possédé en ligne : {provider_name}"
    if kind == "owned" and state == "archived":
        return f"Achat archivé : {provider_name}"
    if kind == "loan" and state == "active":
        return f"Prêt de bibliothèque actif : {provider_name}"
    if kind == "loan" and state in {"expired", "returned", "inactive"}:
        return f"Prêt de bibliothèque non actif : {provider_name}"
    # A missing or contradictory state is not a positive purchase/loan.
    return f"Droit distant non qualifié : {provider_name}" if kind in {"owned", "loan"} else f"Droit distant : {provider_name}"


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
    if item.get("source") == "loan" and item.get("loan_id"):
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
    detail.show_presentation(book_detail_presentation(
        title=str(item.get("title") or "Sans titre"),
        subtitle=str(item.get("author") or ""),
        thumbnail=item.get("thumbnail_url"),
        badges=[],
        section_rows=local_book_detail_sections(
            item,
            provider_display_name=window.service.provider_display_name,
            availability_label=window._library_availability_label,
            identity_by_id=window.service.identity_by_id,
            sync_status_label=window._kobo_backup_status_label,
            language_label=detail_language,
        ),
        links=document_links(item),
        metadata=item.get('metadata'),
    ), description=description)
    placements = list(item.get('placements') or [])
    possessions = list(item.get('possessions') or [])
    presence_rows = []
    for placement in placements:
        iid = str(placement.get('identity_id') or '')
        identity_ref = window.service.identity_by_id(iid) if iid else None
        owner = identity_ref.label if identity_ref else (iid or 'Bibliothèque')
        target = placement.get('storage_target_id') or placement.get('provider_id') or 'local'
        presence_rows.append((owner, f"Stockage : {target}"))
    for possession in possessions:
        iid = str(possession.get('identity_id') or '')
        identity_ref = window.service.identity_by_id(iid) if iid else None
        owner = identity_ref.label if identity_ref else (iid or 'Compte')
        provider_name = window.service.provider_display_name(str(possession.get('provider_id') or ''))
        presence_rows.append((owner, possession_usage_label(possession, provider_name)))
    unique_presence = list(dict.fromkeys(presence_rows))
    if len(unique_presence) > 1:
        detail.add_group('Présence / Bibliothèques / Sources', unique_presence)
    window._add_book_notice(detail,item, include_description=False)
    window._add_local_book_actions(detail,item)
    window._add_book_recommendation_action(detail, item)
    window._add_facets(window.library_page.detail, item, window.library_controls)
    # show_presentation does not decorate dataclass books like show_loan does.
    # Route plugin book actions through the same public registry exactly once.
    window._add_plugin_book_actions(detail, item)
    window._request_cached_book_details("library", row.key, item)
