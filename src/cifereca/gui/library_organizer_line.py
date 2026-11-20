"""#3176: native read-only organizer, consuming the one Core review."""
from __future__ import annotations

from ..core.library_organizer_review import (
    admit_organizer_review, run_organizer_review, select_organizer_keeper_advice,
)
from ..storage import Storage
from ..task_queue import TaskPriority

_REASONS = {
    "single_registered_placement": "Un seul placement enregistré",
    "physical_and_reference_review_required": "Copies à vérifier",
    "placement_recovery_required": "Placement historique ou indisponible",
    "legacy_source_scope_unbound": "Provenance historique à examiner",
    "catalogue_scope_mismatch": "Notice locale incohérente",
    "placement_or_artifact_unverified": "Placement non vérifié",
    "scope_changed": "Contexte ou preuves modifiés",
    "physical_budget": "Budget de vérification atteint",
    "excluded_or_unverified_path": "Chemin exclu ou non vérifié",
    "format_or_bundle_held": "Format non traité ou bundle indivisible",
    "physical_unverified": "Octets non vérifiés",
    "unsupported_filename_convention": "Suffixe non traité",
    "description_unverified": "Description OPF non concordante",
    "pdf_description_unverified": "Description PDF non attestée",
    "naming_policy_unverified": "Nom proposé non vérifiable",
    "receipt_discriminator_preserve": "Identifiant de réception à conserver",
    "name_unchanged": "Nom conservé",
    "destination_occupied": "Destination occupée",
    "rename_advice_only": "Suggestion de renommage",
    "classified_directory_missing": "Dossier inexistant ; aucune création",
    "classified_layout_advice_only": "Disposition indicative ; références à qualifier",
    "classified_target_occupied": "Destination de classement occupée",
    "classified_target_unverified": "Destination de classement non vérifiée",
    "classified_author_unverified": "Auteur non admissible comme dossier",
    "candidate_not_safe_for_retirement": "Dépendances ou preuves insuffisantes",
    "ambiguous_keeper_preference": "Copies de préférence équivalente",
}


def _display(result, selected=0):
    if type(result) is not dict or type(result.get("groups")) is not tuple:
        return "Revue indisponible ou contexte modifié. Aucun constat d’absence."
    advice = result["keeper_advice"]
    lines = [
        f"Page {result['page']} · {result['placement_count']} placements SQL · {result['group_count']} groupes",
        "Aperçu partiel du nommage et des catégories déclarées dans l’OPF ; aucun classement exécuté.",
        "Les dossiers actuels sont conservés. Aucun déplacement ni retrait.",
        f"{len(advice.eligible)} avis keeper présélectionnables sur cette page · {selected} sélectionnés",
    ]
    for number, group in enumerate(result["groups"], 1):
        lines.extend(("", f"Groupe {number} · {group['copies']} copie(s) · "
                      + _REASONS.get(group["reason"], "Revue nécessaire")))
        if group.get("category_evidence") == "verified_opf_type_claim":
            lines.append("Catégorie proposée (type déclaré dans l’OPF vérifié) : "
                         + group["category_advice"])
        else:
            lines.append("Catégorie proposée : Non classés (preuve insuffisante)")
        for file in group["files"]:
            lines.append(file["current"] or "Chemin exclu")
            if file["proposed_name"]:
                lines.append("  Nom conseillé : " + file["proposed_name"])
            lines.append("  " + _REASONS.get(file["reason"], "Revue nécessaire"))
            if file.get("classified_path_advice"):
                lines.append("  Chemin de classement indicatif : " + file["classified_path_advice"])
                lines.append("  " + _REASONS[file["classified_path_reason"]])
            elif file.get("classified_path_reason") in (
                    "classified_target_occupied", "classified_target_unverified",
                    "classified_author_unverified"):
                lines.append("  " + _REASONS[file["classified_path_reason"]])
        if group["keeper"]:
            lines.append("Copie recommandée à conserver : " + group["keeper"])
    if not result["groups"]:
        lines.append("Aucun groupe enregistré dans cette page de l’inventaire SQL.")
    lines.extend(("", "« Tout accepter (avis) » ne sélectionne que des recommandations, "
                  "sans effet sur les fichiers, droits ou provenances."))
    return "\n".join(lines)


def add_library_organizer_line(window, box, *, Gtk, Adw):
    service = getattr(window, "service", None)
    if (not isinstance(getattr(service, "storage", None), Storage)
            or getattr(window, "_closed", False)
            or not callable(getattr(getattr(window, "runner", None), "submit", None))):
        return False
    group = Adw.PreferencesGroup()
    row = Adw.ActionRow()
    row.set_title("Organiser la bibliothèque")
    row.set_subtitle("Revue unique du nommage, du classement et des copies. Lecture seule.")
    start = window.action_button("Prévisualiser…", "system-search-symbolic")
    row.add_suffix(start)
    group.add(row)
    box.append(group)
    actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    following = Gtk.Button(label="Page suivante")
    accept = Gtk.Button(label="Tout accepter (avis)")
    following.set_sensitive(False)
    accept.set_sensitive(False)
    actions.append(following)
    actions.append(accept)
    box.append(actions)
    detail = Gtk.Label(xalign=0)
    detail.set_wrap(True)
    detail.set_selectable(True)
    detail.set_visible(False)
    box.append(detail)

    busy = False
    scope = page = route = generation = None

    def alive():
        return (not getattr(window, "_closed", False) and start.get_root() is not None
                and window.service is service and scope is not None and scope.current()
                and getattr(window, "_route_display_token", None) is route
                and getattr(window, "_identity_context_generation", None) == generation)

    def reset():
        nonlocal page
        page = None
        detail.set_text("")
        detail.set_visible(False)
        start.set_sensitive(True)
        accept.set_sensitive(False)
        following.set_sensitive(False)

    def run(button, *, restart):
        nonlocal scope, page, route, generation, busy
        if busy or getattr(window, "_closed", False) or button.get_root() is None:
            return
        if restart:
            scope = admit_organizer_review(service)
            route = getattr(window, "_route_display_token", None)
            generation = getattr(window, "_identity_context_generation", None)
            cursor = None
        elif alive() and page is not None and page.get("next_cursor") is not None:
            cursor = page["next_cursor"]
        else:
            reset()
            return
        if not alive():
            reset()
            return
        admitted = scope
        page = None
        busy = True
        delivered = False
        for control in (start, following, accept):
            control.set_sensitive(False)
        detail.set_visible(True)
        detail.set_text("Revue locale en cours…")

        def done(value):
            nonlocal busy, page, delivered
            if delivered:
                return
            delivered = True
            busy = False
            if not alive() or scope is not admitted:
                if start.get_root() is not None:
                    reset()
                return
            page = value if type(value) is dict else None
            detail.set_text(_display(page))
            start.set_sensitive(True)
            following.set_sensitive(page is not None and page["next_cursor"] is not None)
            accept.set_sensitive(page is not None and bool(page["keeper_advice"].eligible))

        try:
            window.runner.submit(
                "Revue d’organisation locale…",
                lambda: run_organizer_review(service, admitted, cursor=cursor),
                done, lambda _exc: done(None), control=start,
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )
        except Exception:
            done(None)

    def choose_all(_button):
        if busy or not alive() or page is None:
            if not busy and start.get_root() is not None:
                reset()
            return
        selected = select_organizer_keeper_advice(scope, page, confirmed=True)
        if selected is not None and alive():
            detail.set_text(_display(page, len(selected.preselected)))
            accept.set_sensitive(False)

    start.connect("clicked", lambda b: run(b, restart=True))
    following.connect("clicked", lambda b: run(b, restart=False))
    accept.connect("clicked", choose_all)
    return True
