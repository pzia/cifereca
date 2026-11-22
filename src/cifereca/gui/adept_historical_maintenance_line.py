"""#976: explicit local historical loan review in Outils/Maintenance."""
from __future__ import annotations

from ..core.adept_historical_maintenance import (
    admit_historical_review, run_historical_review, run_historical_hold_review,
    select_historical_hold,
)
from ..core.adept_historical_inventory_operator import admit_inventory_comparison
from ..storage import Storage
from ..task_queue import TaskPriority


def _display(result):
    if type(result) is not dict or type(result.get("rows")) is not tuple:
        return "Revue indisponible ou contexte modifié. Aucun constat d’absence."
    lines = ["Acquisitions historiques enregistrées — aucun état fournisseur actuel vérifié."]
    if not result["rows"]:
        lines.append("Aucune transaction ADEPT enregistrée dans cette page du ledger.")
    for row in result["rows"]:
        def value(key):
            candidate = row.get(key)
            return ("inconnu" if candidate is None
                    else "(vide enregistré)" if candidate == "" else str(candidate))
        pub = row.get("publication") or {}
        physical = row.get("local_bytes") or {}
        evidence = physical.get("evidence") or {}
        lines.extend((
            "",
            f"Acquisition : {value('acquisition_id')} · {value('ledger_status')}",
            f"Source : {value('provider_id')} / {value('credential_id')} / {value('account_id')}",
            f"Édition : {value('edition_id')} · Prêt : {value('provider_loan_id')}",
            f"Profil ADEPT enregistré : {value('recorded_adept_profile_id')}",
            f"Prêt ADEPT enregistré : {value('recorded_adept_loan_id')} · {value('historical_license_state')}",
            f"Branche canonique candidate : {value('canonical_branch')} · {value('canonical_status')}",
            f"Origines : {pub.get('canonical_origin_records', 'not_checked')} · "
            f"Placement : {pub.get('canonical_placement_state', 'not_checked')}",
            f"Octets : {evidence.get('placement_evidence', physical.get('selection', 'not_checked'))}",
        ))
        if row.get("inventory_checked_at") is not None:
            lines.append("Horodatage historique (Unix) : " + value("inventory_checked_at"))
    lines.append("")
    lines.append("Branche et inventaire non attestés par le ledger. Aucun retour, rattachement, acquisition ou suppression autorisé.")
    return "\n".join(lines)


def _display_holds(result):
    if type(result) is not dict or type(result.get("rows")) is not tuple:
        return "Verrous indisponibles ou contexte modifié. Aucun constat d’absence."
    lines = [
        "Verrous de téléchargements ADEPT — observations locales du profil actif.",
        f"Verrous connus : {result['known_holds']} (fenêtre relue à chaque page).",
    ]
    for row in result["rows"]:
        reason = {
            "marker_recorded": "Marqueur de tentative enregistré ; phase non attestée.",
            "historical_ledger": "Attention historique enregistrée ; rejeu non attesté.",
            "unsubmitted_witness_orphan": "Témoin sans marqueur correspondant ; conserver le verrou.",
        }.get(row["hold_origin"], "État du verrou indéterminé.")
        if row["hold_origin"] == "marker_recorded":
            reason = {
                "held_phase_unknown": "Marqueur v1 exact enregistré ; phase non attestée.",
                "held_invalid": "Marqueur illisible ou non canonique ; conserver le verrou.",
            }.get(row.get("marker_state"), "Marqueur non vérifiable ; conserver le verrou.")
        phase = row.get("non_submission_evidence")
        if phase == "non_submission_recorded":
            reason += " " + {
                "core_pre_dispatch_refusal": "Refus Core avant appel runtime enregistré.",
                "runtime_attested_not_submitted": "Non-soumission attestée par le runtime enregistrée.",
            }.get(row.get("non_submission_reason"), "Témoin de non-soumission enregistré.")
        elif phase in ("unverified", "unreadable"):
            reason += " Témoin non vérifié ; aucune reprise."
        lines.extend((
            "",
            row["title"],
            f"Source : {row['provider_id']} / {row['credential_id']} / "
            + (row['account_id'] or "(compte principal)"),
            f"Édition : {row['edition_id']} · Prêt : {row['provider_loan_id']}",
            f"Branche : {row['canonical_branch']}",
            reason,
            {
                "not_requested": "Copie finale non vérifiée ; sélectionnez « Vérifier les fichiers ».",
                "verified_local_copy_observed": (
                    "Copie locale SHA vérifiée ; n'établit ni droit courant "
                    "ni preuve de non-soumission ADEPT."
                ),
                "not_proven": (
                    "Copie finale non prouvée ; ne pas conclure à son absence."
                ),
            }.get(row.get("local_copy_evidence"), "Preuve locale indisponible."),
            "Prêt inactif enregistré." if row["recorded_inactive"] else
            "État fournisseur actuel non vérifié.",
            "Retéléchargement non autorisé : preuve de reprise non établie.",
        ))
    lines.append("\nAucune activation, offre fraîche, présence finale ou preuve de rejeu vérifiée. "
                 "Aucun verrou modifié.")
    return "\n".join(lines)


def _selected_hold_summary(value):
    from ..core.adept_historical_maintenance import HistoricalHoldSelection
    if type(value) is not HistoricalHoldSelection or value.unlock_authorized is not False:
        return "Sélection périmée ou indisponible. Relancer la revue."
    source = value.source
    reason = {
        "final_receipt_precedence": "Un reçu final et ses octets ont été vérifiés pour l’offre historique exacte ; privilégier la reprise canonique sans GET.",
        "local_copy_precedence": "Une copie locale vérifiée a été observée ; privilégier la récupération locale.",
        "recorded_loan_inactive": "Prêt enregistré inactif ; aucune offre actuelle qualifiée.",
        "marker_unverified": "Marqueur historique illisible, manquant ou non vérifiable.",
        "historical_phase_ambiguous": "Phase historique ou preuve de rejeu non établie.",
        "local_recovery_not_qualified": "Témoin exact de non-soumission enregistré ; vérifier d’abord les copies et reçus locaux. Activation historique non attestée.",
        "historical_activation_unattested": "Témoin exact de non-soumission enregistré ; activation historique non attestée. Un identifiant de profil actuel ne démontre pas sa continuité.",
    }.get(value.rearm_blocker, "Preuves de reprise non qualifiées.")
    return ("Verrou sélectionné : " + source.scope.provider_id + " / "
            + source.scope.credential_id + " / " + (source.scope.account_id or "(compte principal)")
            + "\\nÉdition : " + source.edition_id + " · Prêt : " + source.right_id
            + " · Branche : " + source.branch + "\\n" + reason
            + "\\nRetéléchargement non autorisé ; aucun verrou modifié.").replace("\\n", "\n")


def add_adept_historical_maintenance_line(window, box, *, Gtk, Adw, mode="combined",
                                          view_current=lambda: True):
    """Mount the existing controller on one functional surface.

    The flat legacy host retains both actions. The grouped host exposes
    separate provenance and recovery rows with independent ephemeral views,
    not a second hold scanner, queue or writer.
    """
    if (type(mode) is not str or mode not in ("combined", "provenance", "holds")
            or not callable(view_current)):
        return False
    service = getattr(window, "service", None)
    if (not isinstance(getattr(service, "storage", None), Storage)
            or getattr(window, "_closed", False)
            or not callable(getattr(getattr(window, "runner", None), "submit", None))):
        return False
    group = Adw.PreferencesGroup()
    row = Adw.ActionRow()
    row.set_title("Verrous de téléchargements ADEPT" if mode == "holds"
                  else "Provenances de prêts historiques")
    row.set_subtitle("Diagnostic local des verrous ; aucun déverrouillage autorisé."
                     if mode == "holds" else "Rapport local du profil actif, sans accès fournisseur.")
    start = window.action_button("Examiner…", "system-search-symbolic")
    holds = window.action_button("Verrous…", "dialog-warning-symbolic")
    primary = holds if mode == "holds" else start
    if mode != "holds":
        row.add_suffix(start)
    if mode != "provenance":
        row.add_suffix(holds)
    group.add(row)
    box.append(group)
    actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    verify = Gtk.Button(label="Vérifier les fichiers")
    next_page = Gtk.Button(label="Page suivante")
    verify.set_sensitive(False)
    next_page.set_sensitive(False)
    actions.append(verify)
    actions.append(next_page)
    selection = Gtk.ComboBoxText()
    selection.set_sensitive(False)
    compare = Gtk.Button(label="Comparer l’inventaire ADEPT local")
    compare.set_sensitive(False)
    inventory_actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    inventory_actions.append(selection)
    inventory_actions.append(compare)
    box.append(actions)
    box.append(inventory_actions)
    inventory_actions.set_visible(False)
    detail = Gtk.Label(xalign=0)
    detail.set_wrap(True)
    detail.set_selectable(True)
    detail.set_visible(False)
    box.append(detail)
    busy = False
    cursor = ""
    following = None
    last_request = None
    selected_rows = ()
    last_page = None
    last_text = ""
    last_route = None
    last_generation = None
    hold_mode = mode == "holds"

    def mounted():
        try:
            return view_current() is True
        except Exception:
            return False

    def selection_changed(_control):
        compare.set_sensitive(
            mounted() and not busy and 0 <= selection.get_active() < len(selected_rows)
            and last_request is not None and last_request.current()
            and getattr(window, "_route_display_token", None) is last_route
            and getattr(window, "_identity_context_generation", None) == last_generation
        )

    def reset_selection():
        nonlocal selected_rows, last_page
        selected_rows = ()
        last_page = None
        inventory_actions.set_visible(False)
        selection.remove_all()
        selection.set_sensitive(False)
        compare.set_sensitive(False)

    def run(button, *, restart=False, advance=False, hash_files=False, open_holds=False):
        nonlocal busy, cursor, following, last_request, selected_rows, last_text, last_route, last_generation, hold_mode
        if (not mounted() or busy or getattr(window, "_closed", False) or button.get_root() is None
                or (restart and ((mode == "holds" and not open_holds)
                                 or (mode == "provenance" and open_holds)))):
            return
        if restart:
            request = admit_historical_review(service)
            hold_mode = open_holds
            proposed = 0 if hold_mode else ""
        else:
            request = last_request
            proposed = following if advance else cursor
            if request is None or not request.current() or proposed is None:
                detail.set_text("Contexte modifié. Relancer la revue.")
                verify.set_sensitive(False)
                next_page.set_sensitive(False)
                return
        if request is None or window.service is not service:
            return
        generation = getattr(window, "_identity_context_generation", None)
        route = getattr(window, "_route_display_token", None)
        busy = True
        reset_selection()
        last_request = request
        cursor = proposed
        following = None
        for control in (start, holds, verify, next_page):
            control.set_sensitive(False)
        detail.set_visible(True)
        detail.set_text("Revue locale en cours…")

        def current():
            return (mounted() and not getattr(window, "_closed", False) and primary.get_root() is not None
                    and window.service is service and request.current()
                    and getattr(window, "_identity_context_generation", None) == generation
                    and getattr(window, "_route_display_token", None) is route)

        examining_holds = hold_mode
        delivered = False

        def done(value):
            nonlocal busy, following, selected_rows, last_page, last_text, last_route, last_generation, delivered
            if delivered:
                return
            delivered = True
            busy = False
            if not current():
                if mounted() and primary.get_root() is not None:
                    detail.set_text("")
                    detail.set_visible(False)
                    start.set_sensitive(True)
                    holds.set_sensitive(True)
                return
            valid = type(value) is dict and type(value.get("rows")) is tuple
            following = value.get("next_cursor") if valid else None
            last_text = _display_holds(value) if examining_holds else _display(value)
            last_route, last_generation = route, generation
            detail.set_text(last_text)
            selected_rows = tuple(
                item for item in value["rows"]
                if item.get("ledger_status") == "complete"
                and item.get("canonical_status") == "unique_source_record"
                and item.get("recorded_adept_profile_id")
                and item.get("recorded_adept_loan_id")
            ) if valid and not examining_holds else value["rows"] if valid else ()
            last_page = value if valid and examining_holds else None
            compare.set_label("Examiner le verrou sélectionné" if examining_holds
                              else "Comparer l’inventaire ADEPT local")
            for index, item in enumerate(selected_rows, 1):
                if examining_holds:
                    selection.append_text(f"{index}. {item['title']} — {item['provider_id']} / "
                                          f"{item['credential_id']} / {item['provider_loan_id']}")
                else:
                    selection.append_text(
                        f"{index}. Acquisition {item['acquisition_id']} — profil {item['recorded_adept_profile_id']}"
                    )
            inventory_actions.set_visible(bool(selected_rows))
            selection.set_active(-1)
            selection.set_sensitive(bool(selected_rows))
            start.set_sensitive(True)
            holds.set_sensitive(True)
            verify.set_sensitive(valid and bool(value["rows"]))
            next_page.set_sensitive(following is not None)

        try:
            window.runner.submit(
                "Revue des provenances historiques…",
                (lambda: run_historical_hold_review(
                    service, request, offset=proposed, verify_local_bytes=hash_files))
                if examining_holds else
                (lambda: run_historical_review(service, request, cursor=proposed,
                                               verify_local_bytes=hash_files)),
                done, lambda _exc: done(None), control=primary,
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )
        except Exception:
            done(None)

    def compare_clicked(button):
        nonlocal busy
        index = selection.get_active()
        if (not mounted() or busy or not 0 <= index < len(selected_rows)
                or last_request is None or not last_request.current()
                or getattr(window, "_closed", False) or button.get_root() is None
                or window.service is not service
                or getattr(window, "_route_display_token", None) is not last_route
                or getattr(window, "_identity_context_generation", None) != last_generation):
            return
        request, route, generation = last_request, last_route, last_generation
        examining_holds = hold_mode
        page = last_page
        operation = (lambda: select_historical_hold(
            service, request, page, ordinal=index, confirmed=True)
        ) if examining_holds else admit_inventory_comparison(
            service, request, selected_rows[index], explicitly_requested=True,
        )
        if operation is None:
            detail.set_text(last_text + "\n\nInventaire local indisponible.")
            return
        busy = True
        for widget in (start, holds, verify, next_page, selection, compare):
            widget.set_sensitive(False)
        detail.set_text(last_text + "\n\nComparaison locale du prêt sélectionné…")
        delivered = False

        def done(value):
            nonlocal busy, delivered
            if delivered:
                return
            delivered = True
            busy = False
            if (not mounted() or getattr(window, "_closed", False) or button.get_root() is None
                    or window.service is not service or not request.current()
                    or getattr(window, "_route_display_token", None) is not route
                    or getattr(window, "_identity_context_generation", None) != generation):
                if mounted() and button.get_root() is not None:
                    detail.set_text("")
                    detail.set_visible(False)
                    reset_selection()
                    start.set_sensitive(True)
                    holds.set_sensitive(True)
                return
            if examining_holds:
                detail.set_text(last_text + "\n\n" + _selected_hold_summary(value))
                start.set_sensitive(True)
                holds.set_sensitive(True)
                verify.set_sensitive(True)
                next_page.set_sensitive(following is not None)
                selection.set_sensitive(bool(selected_rows))
                selection_changed(selection)
                return
            summary = "Inventaire local indisponible ou preuves modifiées. Aucun constat d’absence."
            if type(value) is dict:
                comparison = {
                    "exact_profile_and_loan_reported": "Prêt exact rapporté dans le profil historique.",
                    "not_found_in_supplied_inventory": "Prêt non retrouvé dans cet inventaire local.",
                    "ambiguous_loan": "Prêt ambigu dans l’inventaire.",
                    "inventory_unverified": "Inventaire non vérifiable.",
                }.get(value.get("inventory_comparison"), "Comparaison indéterminée.")
                state = {
                    "returned_reported": "Restitué selon le registre local.",
                    "expired_reported": "Expiré selon le registre local.",
                    "active_reported": "Actif selon le registre local.",
                    "contradictory_report": "Indications contradictoires.",
                    "state_unverified": "État non vérifié.",
                }.get(value.get("adept_license_state_reported"), "")
                summary = comparison + (" " + state if state else "")
            detail.set_text(last_text + "\n\n" + summary +
                            "\nAucune preuve fournisseur, activation ou autorisation de retour.")
            start.set_sensitive(True)
            holds.set_sensitive(True)
            verify.set_sensitive(True)
            next_page.set_sensitive(following is not None)
            selection.set_sensitive(bool(selected_rows))
            selection_changed(selection)

        try:
            window.runner.submit(
                "Comparaison de l’inventaire ADEPT local…", operation,
                done, lambda _exc: done(None), control=compare,
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )
        except Exception:
            done(None)

    selection.connect("changed", selection_changed)
    compare.connect("clicked", compare_clicked)
    if mode != "holds":
        start.connect("clicked", lambda b: run(b, restart=True))
    if mode != "provenance":
        holds.connect("clicked", lambda b: run(b, restart=True, open_holds=True))
    verify.connect("clicked", lambda b: run(b, hash_files=True))
    next_page.connect("clicked", lambda b: run(b, advance=True))
    return True
