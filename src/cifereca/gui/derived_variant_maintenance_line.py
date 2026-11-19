"""#4520: Core-owned read-only Maintenance line; no recovery button."""
from __future__ import annotations

from ..core.derived_variant_maintenance_review import (
    admit_maintenance_review, read_maintenance_review,
)
from ..storage import Storage
from ..task_queue import TaskPriority


def _subtitle(result):
    if type(result) is not dict or result.get("status") != "observed":
        return "Revue indisponible ou contexte modifié. Aucun constat d’absence."
    counts = result.get("counts")
    checked = result.get("checked")
    if (type(counts) is not dict or type(checked) is not int or not 0 <= checked <= 4
            or set(counts) != {"linked_pending", "staged_only", "manual_review"}
            or any(type(n) is not int or not 0 <= n <= 4 for n in counts.values())
            or sum(counts.values()) != checked):
        return "Revue indisponible. Aucun constat d’absence."
    coverage = ("Page des reçus examinée." if result.get("complete_pending_page") is True
                else "Revue partielle, au plus quatre reçus.")
    return (
        f"{checked} reçu(s) examiné(s) : {counts['linked_pending']} déjà lié(s), "
        f"{counts['staged_only']} en staging, {counts['manual_review']} à vérifier. "
        f"{coverage} Aucune reprise ni suppression autorisée."
    )


def add_derived_variant_maintenance_line(window, box, *, Gtk, Adw):
    service = getattr(window, "service", None)
    if (not isinstance(getattr(service, "storage", None), Storage)
            or getattr(window, "_closed", False)
            or not callable(getattr(getattr(window, "runner", None), "submit", None))):
        return False
    group = Adw.PreferencesGroup()
    row = Adw.ActionRow()
    row.set_title("Publications dérivées interrompues")
    row.set_subtitle("Examiner les reçus du profil actif en lecture seule.")
    button = window.action_button("Examiner…", "system-search-symbolic")
    row.add_suffix(button)
    group.add(row)
    box.append(group)
    busy = False

    def clicked(_button):
        nonlocal busy
        if busy or getattr(window, "_closed", False) or button.get_root() is None:
            return
        request = admit_maintenance_review(service)
        if request is None or window.service is not service:
            return
        generation = getattr(window, "_identity_context_generation", None)
        route = getattr(window, "_route_display_token", None)
        busy = True
        row.set_subtitle("Revue des reçus en cours…")

        def current():
            return (
                not getattr(window, "_closed", False) and button.get_root() is not None
                and window.service is service and request.current()
                and getattr(window, "_identity_context_generation", None) == generation
                and getattr(window, "_route_display_token", None) is route
            )

        def done(value):
            nonlocal busy
            busy = False
            if current():
                row.set_subtitle(_subtitle(value))

        try:
            window.runner.submit(
                "Revue des publications dérivées…",
                lambda: read_maintenance_review(service, request),
                done, lambda _exc: done(None), control=button,
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )
        except Exception:
            done(None)

    button.connect("clicked", clicked)
    return True
