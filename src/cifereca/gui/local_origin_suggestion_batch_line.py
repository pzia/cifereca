"""#4030: supervised Maintenance pages, known public C only."""
from __future__ import annotations

from threading import Event

from ..core.local_origin_suggestion_batch import admit_origin_batch, run_origin_batch
from ..storage import Storage
from ..task_queue import TaskPriority
from .local_origin_suggestion_action import _LABELS


def _display(page):
    if page is None:
        return "Revue arrêtée ou preuves modifiées. Relancer explicitement."
    lines = [f"Page {page['page']} · {len(page['entries'])} placement(s) candidat(s)",
             "Notices publiques déjà connues uniquement ; couverture partielle.",
             "Aucune origine, association ou licence créée."]
    for row in page["entries"]:
        lines.extend(("", row["file"]))
        if row["status"] != "compared":
            lines.append("Non évalué : budget, chemin ou preuves insuffisantes.")
            continue
        lines.append(f"{row['known_matches']} correspondance(s) connue(s) ; absence non prouvée.")
        for candidate in row["suggestions"]:
            # The plugin batch also carries six documentary C coordinates.
            # Keep the legacy five-field GTK projection until its own contract evolves.
            title, author, provider, comparison, stale = candidate[:5]
            lines.append(" · ".join(str(part) for part in (
                title, author, provider, _LABELS.get(comparison, "Comparaison descriptive"),
                "Notice ancienne" if stale else "Notice en cache",
            ) if part))
        if row["more_matches"]:
            lines.append("Autres correspondances : poursuivre depuis la fiche Bibliothèque.")
    if not page["entries"]:
        lines.append("Aucun candidat enregistré sur cette page ; couverture physique non attestée.")
    if page["has_more"] and page["next_cursor"] is None:
        lines.append("Limite de revue atteinte ; d’autres placements peuvent exister.")
    return "\n".join(lines)


def add_local_origin_suggestion_batch_line(window, box, *, Gtk, Adw):
    service = getattr(window, "service", None)
    if (not isinstance(getattr(service, "storage", None), Storage)
            or getattr(window, "_closed", False)
            or not callable(getattr(getattr(window, "runner", None), "submit", None))):
        return False
    group, row = Adw.PreferencesGroup(), Adw.ActionRow()
    row.set_title("Suggestions de provenance — par lots")
    row.set_subtitle("Comparer les fichiers sans origine connue aux notices publiques en cache. Lecture seule.")
    start = window.action_button("Prévisualiser…", "system-search-symbolic")
    row.add_suffix(start)
    group.add(row)
    box.append(group)
    actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    following, cancel = Gtk.Button(label="Page suivante"), Gtk.Button(label="Annuler")
    following.set_sensitive(False)
    cancel.set_sensitive(False)
    actions.append(following)
    actions.append(cancel)
    box.append(actions)
    detail = Gtk.Label(xalign=0)
    detail.set_wrap(True)
    detail.set_selectable(True)
    detail.set_visible(False)
    box.append(detail)

    scope = cursor = route = stop = None
    busy = False

    def context_current():
        return (not getattr(window, "_closed", False) and window.service is service
                and getattr(window, "_route_display_token", None) is route
                and scope is not None and scope.current())

    def attached():
        return not getattr(window, "_closed", False) and start.get_root() is not None

    def run(_button, restart):
        nonlocal scope, cursor, route, stop, busy
        if busy or not attached():
            return
        if restart:
            scope = admit_origin_batch(service, confirmed=True)
            route = getattr(window, "_route_display_token", None)
            cursor = None
        elif cursor is None:
            return
        if not context_current():
            detail.set_visible(True)
            detail.set_text(_display(None))
            following.set_sensitive(False)
            return
        admitted, position, token = scope, cursor, route
        stop = event = Event()
        busy, delivered = True, False
        start.set_sensitive(False)
        following.set_sensitive(False)
        cancel.set_sensitive(True)
        detail.set_visible(True)
        detail.set_text("Comparaison documentaire en cours…")

        def cancelled():
            return (event.is_set() or getattr(window, "_closed", False)
                    or window.service is not service
                    or getattr(window, "_route_display_token", None) is not token)

        def done(result):
            nonlocal busy, cursor, stop, delivered
            if delivered:
                return
            delivered, busy, stop = True, False, None
            if not attached():
                return
            if not context_current() or admitted is not scope:
                # Detached or stale results must not render the old profile.
                detail.set_visible(False)
                detail.set_text("")
                cursor = None
            else:
                if event.is_set():
                    result = None
                detail.set_text(_display(result))
                cursor = result["next_cursor"] if result is not None else None
            start.set_sensitive(True)
            cancel.set_sensitive(False)
            following.set_sensitive(cursor is not None)

        try:
            window.runner.submit(
                "Comparaison documentaire par lots…",
                lambda: run_origin_batch(service, admitted, cursor=position, cancelled=cancelled),
                done, lambda _exc: done(None), control=start,
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )
        except Exception:
            done(None)

    def request_cancel(_button):
        if stop is not None and attached():
            stop.set()
            cancel.set_sensitive(False)
            detail.set_text("Arrêt demandé…")

    start.connect("clicked", lambda button: run(button, True))
    following.connect("clicked", lambda button: run(button, False))
    cancel.connect("clicked", request_cancel)
    return True
