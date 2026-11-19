"""#976: explicit local historical loan review in Outils/Maintenance."""
from __future__ import annotations

from ..core.adept_historical_maintenance import admit_historical_review, run_historical_review
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
            f"Licence enregistrée : {value('recorded_adept_loan_id')} · {value('historical_license_state')}",
            f"Branche : {value('canonical_branch')} · {value('canonical_status')}",
            f"Origines : {pub.get('canonical_origin_records', 'not_checked')} · "
            f"Placement : {pub.get('canonical_placement_state', 'not_checked')}",
            f"Octets : {evidence.get('placement_evidence', physical.get('selection', 'not_checked'))}",
        ))
        if row.get("inventory_checked_at") is not None:
            lines.append("Horodatage historique (Unix) : " + value("inventory_checked_at"))
    lines.append("")
    lines.append("Autres observations possibles. Aucun retour, rattachement, acquisition ou suppression autorisé.")
    return "\n".join(lines)


def add_adept_historical_maintenance_line(window, box, *, Gtk, Adw):
    service = getattr(window, "service", None)
    if (not isinstance(getattr(service, "storage", None), Storage)
            or getattr(window, "_closed", False)
            or not callable(getattr(getattr(window, "runner", None), "submit", None))):
        return False
    group = Adw.PreferencesGroup()
    row = Adw.ActionRow()
    row.set_title("Provenances de prêts historiques")
    row.set_subtitle("Rapport local du profil actif, sans accès fournisseur.")
    start = window.action_button("Examiner…", "system-search-symbolic")
    row.add_suffix(start)
    group.add(row)
    box.append(group)
    actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    verify = Gtk.Button(label="Vérifier les fichiers")
    next_page = Gtk.Button(label="Page suivante")
    verify.set_sensitive(False)
    next_page.set_sensitive(False)
    actions.append(verify)
    actions.append(next_page)
    box.append(actions)
    detail = Gtk.Label(xalign=0)
    detail.set_wrap(True)
    detail.set_selectable(True)
    detail.set_visible(False)
    box.append(detail)
    busy = False
    cursor = ""
    following = None
    last_request = None

    def run(button, *, restart=False, advance=False, hash_files=False):
        nonlocal busy, cursor, following, last_request
        if busy or getattr(window, "_closed", False) or button.get_root() is None:
            return
        if restart:
            request = admit_historical_review(service)
            proposed = ""
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
        last_request = request
        cursor = proposed
        following = None
        for control in (start, verify, next_page):
            control.set_sensitive(False)
        detail.set_visible(True)
        detail.set_text("Revue locale en cours…")

        def current():
            return (not getattr(window, "_closed", False) and start.get_root() is not None
                    and window.service is service and request.current()
                    and getattr(window, "_identity_context_generation", None) == generation
                    and getattr(window, "_route_display_token", None) is route)

        def done(value):
            nonlocal busy, following
            busy = False
            if not current():
                if start.get_root() is not None:
                    detail.set_text("")
                    detail.set_visible(False)
                    start.set_sensitive(True)
                return
            valid = type(value) is dict and type(value.get("rows")) is tuple
            following = value.get("next_cursor") if valid else None
            detail.set_text(_display(value))
            start.set_sensitive(True)
            verify.set_sensitive(valid and bool(value["rows"]))
            next_page.set_sensitive(following is not None)

        try:
            window.runner.submit(
                "Revue des provenances historiques…",
                lambda: run_historical_review(service, request, cursor=proposed,
                                              verify_local_bytes=hash_files),
                done, lambda _exc: done(None), control=start,
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )
        except Exception:
            done(None)

    start.connect("clicked", lambda b: run(b, restart=True))
    verify.connect("clicked", lambda b: run(b, hash_files=True))
    next_page.connect("clicked", lambda b: run(b, advance=True))
    return True
