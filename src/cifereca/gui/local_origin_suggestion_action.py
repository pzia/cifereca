"""#4030: explicit Library-only preview of known public documentary matches."""
from __future__ import annotations

from datetime import datetime, timezone

from ..core.local_origin_suggestion_operator import (
    admit_local_origin_suggestion, continue_local_origin_suggestion,
    run_local_origin_suggestion,
)
from ..core.local_origin_documentary_suggestions import DocumentarySuggestionPage
from ..core.local_origin_public_catalogue import (
    PublicCatalogueCandidate, PublicCatalogueChoice, available_local_public_catalogues,
    search_local_public_catalogue,
)
from ..storage import Storage
from ..task_queue import TaskPriority
from .local_public_c_detail_guard import local_public_web_selection_check


_LABELS = {
    "title_related": "Titre voisin",
    "title_concordant_author_unknown": "Titre concordant, auteur inconnu",
    "title_concordant_author_divergent": "Titre concordant, auteur divergent",
    "title_author_concordant": "Titre et auteur concordants",
    "isbn_title_author_concordant": "ISBN déclaré, titre et auteur concordants",
    "isbn_claim_ambiguous": "ISBN déclaré ambigu",
    "isbn_claim_divergent": "ISBN déclarés divergents",
    "title_concordant_author_unverified": "Titre concordant, auteur non vérifié",
}


def _known_c_observation_label(timestamp):
    """Show the retained C date, never claim current provider freshness."""
    if type(timestamp) is not int or timestamp < 0:
        return "Dernière observation C : date indisponible"
    try:
        when = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "Dernière observation C : date indisponible"
    return f"Dernière observation C (UTC) : {when:%Y-%m-%d %H:%M}"


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
    finished = False
    next_request = None
    captured_route = None

    def active():
        return current() and not getattr(button, "_cifereca_action_context_revoked", False)

    def worker_current(route, request):
        # No GTK reads in the worker. The original route and selection token
        # are inert coordinates; the Core request owns identity/epoch checks.
        return (not getattr(window, "_closed", False)
                and window.service is core and core.storage is storage
                and getattr(window, "_route_display_token", None) is route
                and getattr(window, "_public_web_selection_library", None) is token
                and request.current())

    public_busy = False

    def add_public_actions(request, route, choices):
        if type(choices) is not tuple:
            return
        for index, choice in enumerate(choices[:8]):
            if type(choice) is not PublicCatalogueChoice or choice.request is not request:
                continue
            public_button = window.action_button(
                "Envoyer le titre à " + choice.provider_id, "system-search-symbolic",
            )
            public_button._cifereca_action_id = "local-origin-public-" + str(index)
            public_button._cifereca_action_priority = 40
            public_button.set_tooltip_text(
                "Recherche explicite dans ce catalogue public ; seul le titre OPF est transmis. "
                "Ni compte privé, ni téléchargement, ni attribution d'origine."
            )
            consumed = [False]

            def lookup(clicked_button, selected=choice, used=consumed):
                nonlocal public_busy
                def valid():
                    mapped = getattr(clicked_button, "get_mapped", None)
                    return (active() and worker_current(route, request)
                            and not getattr(clicked_button, "_cifereca_action_context_revoked", False)
                            and (not callable(mapped) or mapped()))
                if public_busy or used[0] or not valid():
                    return
                public_busy = True
                used[0] = True
                delivered = False

                def finish(report):
                    nonlocal public_busy, delivered
                    if delivered:
                        return
                    delivered = True
                    public_busy = False
                    if not valid():
                        return
                    if (type(report) is not dict or report.get("status") != "partial"
                            or type(report.get("candidates")) is not tuple):
                        reason = ({
                        "auth_required": "Authentification requise",
                        "access_denied": "Accès refusé",
                        "rate_limited": "Limite de requêtes atteinte",
                        "timeout": "Délai de réponse dépassé",
                        "cancelled": "Comparaison annulée",
                    }.get(report.get("status"))
                                  if type(report) is dict and type(report.get("status")) is str else None)
                        window.toast((reason or "Comparaison publique indisponible") +
                                     " ; aucune absence attestée. Nouvelle revue nécessaire.")
                        return
                    window_status = report.get("window_status")
                    window_label = ("Complétude de la réponse non établie." if type(window_status) is not str else {
                        "incomplete": "Réponse catalogue partielle.",
                        "declared_complete": "Réponse déclarée complète pour cette requête.",
                    }.get(window_status, "Complétude de la réponse non établie."))
                    lines = [
                        ("Portée", "Comparaison documentaire uniquement ; aucune origine ni droit attestés."),
                        ("Couverture", window_label + " Fraîcheur réseau non vérifiée."),
                    ]
                    for entry in report["candidates"]:
                        if type(entry) is not PublicCatalogueCandidate or entry.provider_id != selected.provider_id:
                            continue
                        # Preserve public response coordinates on each row:
                        # equal ISBN/record IDs can still denote distinct formats.
                        lines.append((entry.title, " · ".join(part for part in (
                            entry.author, entry.provider_id,
                            "Référence catalogue : " + entry.record_id,
                            "Collection catalogue : " + entry.docbase,
                            "Support catalogue : " + entry.medium,
                            "Format catalogue : " + entry.media_format if entry.media_format else "",
                            "ISBN déclarés par le catalogue : " + ", ".join(entry.isbns) if entry.isbns else "",
                            _LABELS.get(entry.comparison, "Comparaison descriptive"),
                        ) if part)))
                    if len(lines) == 2:
                        lines.append(("Résultat", "Aucune correspondance reçue ; absence non prouvée."))
                    if valid():
                        detail.add_group("Catalogue public — " + selected.provider_id, lines)

                def work():
                    return search_local_public_catalogue(
                        core, request, provider_id=selected.provider_id, confirmed=True,
                        choice=selected, still_current=lambda: worker_current(route, request),
                    )

                try:
                    window.runner.submit(
                        "Comparaison catalogue public…", work, finish,
                        lambda _exc: finish(None), control=clicked_button,
                        priority=TaskPriority.USER_ACTION, kind="local-origin-public-search", origin="user",
                    )
                except Exception:
                    finish(None)

            public_button.connect("clicked", lookup)
            detail.add_actions([public_button])

    def clicked(_button):
        nonlocal busy, finished, next_request, captured_route
        if busy or finished or not active():
            return
        mapped = getattr(button, "get_mapped", None)
        if callable(mapped) and not mapped():
            return
        route = getattr(window, "_route_display_token", None)
        if next_request is None:
            request = admit_local_origin_suggestion(
                core, identity_id=owner, placement_id=placement, confirmed=True,
            )
            captured_route = route
        else:
            request = next_request
            if route is not captured_route or not request.current():
                finished = True
                window.toast("Contexte modifié. Rouvrir la fiche pour une nouvelle revue.")
                return
        if request is None or not active():
            if active():
                window.toast("Vérification de provenance indisponible pour ce profil.")
            return
        busy = True
        delivered = False

        def deliverable():
            return (active() and getattr(window, "_route_display_token", None) is route
                    and request.current())

        def failed(_exc):
            nonlocal busy, delivered
            if delivered:
                return
            delivered = True
            busy = False
            if deliverable():
                window.toast("Vérification de provenance indisponible.")

        def done(result):
            nonlocal busy, finished, next_request, delivered
            if delivered:
                return
            delivered = True
            busy = False
            if not deliverable():
                return
            page = result.get("suggestions") if type(result) is dict else None
            if (type(page) is not DocumentarySuggestionPage or page.page != request.page
                    or page.page_size != request.page_size):
                if request.page:
                    finished = True
                    window.toast("Preuves modifiées ou indisponibles. Rouvrir la fiche pour relancer la revue.")
                else:
                    window.toast("Aucun candidat vérifié pour cette recherche documentaire.")
                return
            following = continue_local_origin_suggestion(core, request, result)
            lines = [
                ("Portée", "Notices publiques connues uniquement. Correspondances documentaires, aucune origine ni droit attestés."),
                ("Couverture", "Partielle ; recherche non exhaustive. Les pages sont relues indépendamment et les résultats peuvent évoluer."),
                ("Page", str(page.page + 1)),
                ("Résultats connus", str(page.known_matches)),
            ]
            if following is not None:
                lines.append(("Suite", "Cliquer de nouveau sur Suggestions de provenance pour la page suivante."))
            elif page.has_more:
                lines.append(("Limite", "Limite de revue atteinte ; d’autres résultats peuvent exister."))
            for entry in page.suggestions:
                hit = entry.hit
                text = " · ".join(part for part in (
                    hit.author, hit.provider_id, hit.record_id,
                    "Édition C : " + hit.edition_id if hit.edition_id else "Édition C non précisée",
                    "Révision C : " + hit.source_revision if hit.source_revision else "",
                    "Support C : " + hit.medium if hit.medium else "",
                    "Format C : " + hit.media_format if hit.media_format else "",
                    _LABELS.get(entry.comparison, "Comparaison descriptive"),
                    "Notice ancienne" if hit.stale else "Notice en cache",
                    _known_c_observation_label(hit.last_seen_at),
                ) if part)
                lines.append((hit.title, text))
            if not page.suggestions:
                # C may contain known matches excluded from this local view
                # because their labels/coordinates are not displayable. An
                # empty rendered window is not zero C evidence.
                verdict = ("Correspondances C connues, aucune fiche présentable sur cette page ; "
                           "absence non prouvée." if page.known_matches > 0 else
                           "Aucune correspondance connue ; absence non prouvée.")
                lines.append(("Résultat", verdict))
            if deliverable():
                detail.add_group("Suggestions de provenance — lecture seule", lines)
                next_request = following
                finished = following is None
                if following is not None:
                    button.set_tooltip_text("Afficher la page suivante des correspondances documentaires connues.")
                if request.page == 0:
                    add_public_actions(request, route, result.get("public_choices"))

        def work():
            result = run_local_origin_suggestion(core, request)
            if (request.page == 0 and worker_current(route, request)
                    and type(result) is dict and result.get("status") == "partial"
                    and result.get("metadata_evidence") == "verified_epub_opf_description"):
                return {**result, "public_choices": available_local_public_catalogues(
                    core, request, evidence=result.get("public_comparison_evidence"),
                )}
            return result

        try:
            window.runner.submit(
                "Comparaison documentaire locale…",
                work,
                done, failed, control=button, priority=TaskPriority.USER_ACTION,
                kind="detail-cache", origin="user",
            )
        except Exception:
            failed(None)

    button.connect("clicked", clicked)
    detail.add_actions([button])
    return True
