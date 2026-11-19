"""#4520: private GTK receipt choice and targeted review; no recovery action."""
from __future__ import annotations

from ..core.derived_variant_maintenance_review import admit_maintenance_review
from ..core.derived_variant_recovery_selection import (
    RecoveryChoicePage, ReviewedRecoveryChoice, recovery_choices, review_recovery_choice,
)
from ..task_queue import TaskPriority


def _choice_subtitle(choice):
    if choice.parent_relative is None or choice.target_relative is None:
        return "Coordonnées indisponibles ; revue impossible."
    return (
        f"Source : {choice.parent_relative}\nSortie : {choice.target_relative}\n"
        f"{choice.media_format} · {choice.output_size} octets (déclarés, non vérifiés)"
    )


def add_derived_variant_selection_line(window, box, *, Adw):
    service = window.service
    group = Adw.PreferencesGroup()
    header = Adw.ActionRow()
    header.set_title("Sélection des publications interrompues")
    header.set_subtitle("Choisir un reçu du profil actif et vérifier ses preuves, sans reprise.")
    start = window.action_button("Lister…", "system-search-symbolic")
    header.add_suffix(start)
    group.add(header)
    box.append(group)
    rows = []
    busy = False
    active = None

    def clear():
        nonlocal active
        for row in rows:
            group.remove(row)
        rows.clear()
        active = None

    def alive():
        return not getattr(window, "_closed", False) and start.get_root() is not None

    def current(request, generation, route):
        return (
            alive() and window.service is service and request.current()
            and getattr(window, "_identity_context_generation", None) == generation
            and getattr(window, "_route_display_token", None) is route
        )

    def epoch():
        request = admit_maintenance_review(service)
        if request is None:
            return None
        return (request, getattr(window, "_identity_context_generation", None),
                getattr(window, "_route_display_token", None))

    def submit(captured, work, finish):
        nonlocal busy
        if busy or captured is None or not current(*captured):
            return
        busy = True

        def guarded_work():
            if not captured[0].current():
                return None
            return work()

        def done(value):
            nonlocal busy
            busy = False
            if current(*captured):
                finish(value)
            elif alive():
                clear()
                header.set_subtitle("Contexte modifié ; relancer la liste.")

        try:
            window.runner.submit(
                "Revue privée des publications…", guarded_work, done,
                lambda _exc: done(None), control=start,
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )
        except Exception:
            done(None)

    def list_page(captured, previous=None):
        if busy or captured is None or not current(*captured):
            return
        clear()
        header.set_subtitle("Liste des reçus en cours…")
        request = captured[0]
        submit(captured, lambda: recovery_choices(
            service, identity_id=request.identity_id, explicitly_requested=True,
            previous=previous,
        ), lambda value: render(captured, value))

    def render(captured, page):
        nonlocal active
        if type(page) is not RecoveryChoicePage:
            header.set_subtitle("Liste indisponible ; aucun constat d’absence.")
            return
        active = page
        header.set_subtitle(
            f"{len(page.choices)} reçu(s) dans cette page, coordonnées non vérifiées. "
            "Aucune reprise ni suppression."
        )
        for index, choice in enumerate(page.choices, 1):
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(f"Reçu {index}")
            row.set_subtitle(_choice_subtitle(choice))
            if choice.parent_relative is not None and choice.target_relative is not None:
                review = window.action_button("Vérifier", "system-search-symbolic")
                row.add_suffix(review)
                review.connect("clicked", lambda _btn, item=choice, target=row:
                               check(captured, page, item, target))
            rows.append(row)
            group.add(row)
        if page.next_cursor is not None:
            row = Adw.ActionRow()
            row.set_title("Autres reçus")
            row.set_subtitle("La liste peut évoluer entre deux pages.")
            more = window.action_button("Page suivante", "go-next-symbolic")
            row.add_suffix(more)
            rows.append(row)
            group.add(row)
            more.connect("clicked", lambda _btn: (
                list_page(captured, page) if active is page else None
            ))

    def check(captured, page, choice, row):
        if busy or active is not page or not alive():
            return
        if not current(*captured):
            clear()
            header.set_subtitle("Contexte modifié ; relancer la liste.")
            return
        row.set_subtitle(_choice_subtitle(choice) + "\nVérification en cours…")

        def finish(result):
            if active is not page:
                return
            verified = type(result) is ReviewedRecoveryChoice and result.choice is choice
            message = (
                "Lien physique vérifié au moment de la revue ; aucune reprise effectuée."
                if verified else "Preuve insuffisante ou devenue périmée ; aucune reprise."
            )
            row.set_subtitle(_choice_subtitle(choice) + "\n" + message)

        submit(captured, lambda: review_recovery_choice(
            service, page, choice, explicitly_requested=True,
        ), finish)

    def start_clicked(_btn):
        if busy or not alive():
            return
        captured = epoch()
        if captured is None or not current(*captured):
            clear()
            header.set_subtitle("Contexte indisponible ; relancer la liste.")
            return
        list_page(captured)

    start.connect("clicked", start_clicked)
