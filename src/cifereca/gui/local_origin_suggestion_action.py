"""#4030: explicit Library-only preview of known public documentary matches."""
from __future__ import annotations

from ..core.local_origin_suggestion_operator import (
    admit_local_origin_suggestion, run_local_origin_suggestion,
)
from ..core.local_origin_documentary_suggestions import DocumentarySuggestionPage
from ..storage import Storage
from ..task_queue import TaskPriority
from .local_public_c_detail_guard import local_public_web_selection_check


_LABELS = {
    "title_related": "Titre voisin",
    "title_concordant_author_unknown": "Titre concordant, auteur inconnu",
    "title_concordant_author_divergent": "Titre concordant, auteur divergent",
    "title_author_concordant": "Titre et auteur concordants",
}


def add_local_origin_suggestion_action(window, detail, row, item, *, token):
    """No worker nor SQL on rendering. Core decides candidacy on explicit click."""
    core = getattr(window, "service", None)
    storage = getattr(core, "storage", None)
    owner = getattr(getattr(core, "current_identity", None), "identity_id", None)
    if (not isinstance(storage, Storage) or type(item) is not dict
            or type(owner) is not str or not owner
            or item.get("identity_id") != owner
            or item.get("_library_view_owner") not in (None, owner)
            or item.get("local") is not True
            or item.get("_public_canonical_document_link") is not None
            or item.get("_public_canonical_cover_link") is not None
            or not callable(getattr(getattr(window, "runner", None), "submit", None))
            or not callable(getattr(window, "action_button", None))
            or not callable(getattr(detail, "add_group", None))):
        return False
    placement = item.get("placement_id")
    key = getattr(row, "key", None)
    if type(placement) is not str or not placement or type(key) is not str or not key:
        return False

    def exact_row():
        return (
            window.service is core and core.storage is storage
            and getattr(getattr(core, "current_identity", None), "identity_id", None) == owner
            and item.get("identity_id") == owner and item.get("placement_id") == placement
            and item.get("_library_view_owner") in (None, owner)
            and getattr(window, "_library_objects", {}).get(key) is item
            and key not in getattr(window, "_library_ambiguous_keys", ())
            and not getattr(row, "_library_ambiguous_key", False)
        )

    current = local_public_web_selection_check(
        window, row, view="library", token=token, source_current=exact_row,
    )
    if not current():
        return False
    button = window.action_button("Suggestions de provenance", "system-search-symbolic")
    button._cifereca_action_id = "local-origin-suggestions"
    button._cifereca_action_priority = 35
    button.set_tooltip_text(
        "Comparer ce fichier aux notices publiques déjà connues ; ni recherche réseau, ni attribution d'origine."
    )
    busy = False
    displayed = False

    def active():
        return current() and not getattr(button, "_cifereca_action_context_revoked", False)

    def clicked(_button):
        nonlocal busy
        if busy or displayed or not active():
            return
        mapped = getattr(button, "get_mapped", None)
        if callable(mapped) and not mapped():
            return
        route = getattr(window, "_route_display_token", None)
        request = admit_local_origin_suggestion(
            core, identity_id=owner, placement_id=placement, confirmed=True,
        )
        if request is None or not active():
            if active():
                window.toast("Vérification de provenance indisponible pour ce profil.")
            return
        busy = True

        def deliverable():
            return (active() and getattr(window, "_route_display_token", None) is route
                    and request.current())

        def failed(_exc):
            nonlocal busy
            busy = False
            if deliverable():
                window.toast("Vérification de provenance indisponible.")

        def done(result):
            nonlocal busy, displayed
            busy = False
            if not deliverable():
                return
            page = result.get("suggestions") if type(result) is dict else None
            if type(page) is not DocumentarySuggestionPage:
                window.toast("Aucun candidat vérifié pour cette recherche documentaire.")
                return
            lines = [
                ("Portée", "Notices publiques connues uniquement. Correspondances documentaires, aucune origine ni droit attestés."),
                ("Couverture", "Partielle ; recherche non exhaustive."),
                ("Résultats connus", str(page.known_matches)),
            ]
            if page.has_more:
                lines.append(("Limite", "Première page seulement ; d’autres résultats peuvent exister."))
            for entry in page.suggestions:
                hit = entry.hit
                text = " · ".join(part for part in (
                    hit.author, hit.provider_id, hit.record_id,
                    _LABELS.get(entry.comparison, "Comparaison descriptive"),
                    "Notice ancienne" if hit.stale else "Notice en cache",
                ) if part)
                lines.append((hit.title, text))
            if not page.suggestions:
                lines.append(("Résultat", "Aucune correspondance connue ; absence non prouvée."))
            if deliverable():
                detail.add_group("Suggestions de provenance — lecture seule", lines)
                displayed = True

        try:
            window.runner.submit(
                "Comparaison documentaire locale…",
                lambda: run_local_origin_suggestion(core, request),
                done, failed, control=button, priority=TaskPriority.USER_ACTION,
                kind="detail-cache", origin="user",
            )
        except Exception:
            failed(None)

    button.connect("clicked", clicked)
    detail.add_actions([button])
    return True
