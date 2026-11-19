"""#6937: operator-only capacity read, separate from recovery and retention."""
from __future__ import annotations

from ..core.derived_variant_capacity_review import review_derived_capacity
from ..core.derived_variant_maintenance_review import admit_maintenance_review
from ..task_queue import TaskPriority


def _subtitle(result):
    if (type(result) is not dict
            or result.get("status") not in ("observed", "limit_reached", "over_capacity")):
        return "Capacité non vérifiable ou contexte modifié ; aucun constat de place libre."
    installation, owner, limits = (result[k] for k in ("installation", "profile", "limits"))
    unbound = result["scope_unverified_receipts"]
    state = {"observed": "Compteurs observés", "limit_reached": "Limite atteinte",
             "over_capacity": "Plafond dépassé"}[result["status"]]
    lines = [
        f"{state} · installation : {installation['records']}/{limits['records']} reçus, "
        f"{installation['payload_bytes']}/{limits['payload_bytes']} octets de reçu, "
        f"{installation['reserved_output_bytes']}/{limits['reserved_output_bytes']} octets logiques réservés.",
        f"Profil actif : {owner['pending']} en cours, {owner['committed']} terminés, "
        f"{owner['reserved_output_bytes']} octets logiques réservés (pas de quota individuel).",
        f"{unbound['records']} reçu(s) de l’installation à portée non vérifiée, conservés dans le quota.",
    ]
    stock = result.get("profile_named_stock")
    if type(stock) is dict and stock.get("status") in ("partial", "journal_entries_observed"):
        lines.append(
            f"Stock nommé observé du profil : {stock['observed_unique_inode_logical_bytes']} octets logiques, "
            f"{stock['observed_unique_inode_allocated_bytes']} octets alloués ; "
            "observation partielle, sans preuve SHA ni estimation de récupération."
        )
    else:
        lines.append("Stock physique nommé non vérifiable.")
    lines.append("Ni réservation, ni remboursement, ni rétention, ni suppression autorisés.")
    return "\n".join(lines)


def add_derived_capacity_line(window, group, *, Adw):
    service = window.service
    row = Adw.ActionRow()
    row.set_title("Capacité du journal des variantes")
    row.set_subtitle("Compteurs de l’installation et stock nommé du profil actif, en lecture seule.")
    button = window.action_button("Mesurer…", "system-search-symbolic")
    row.add_suffix(button)
    group.add(row)
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
        delivered = False
        row.set_subtitle("Mesure locale en cours…")

        def current():
            return (not getattr(window, "_closed", False) and button.get_root() is not None
                    and window.service is service and request.current()
                    and getattr(window, "_identity_context_generation", None) == generation
                    and getattr(window, "_route_display_token", None) is route)

        def done(result):
            nonlocal busy, delivered
            if delivered:
                return
            delivered = True
            busy = False
            if current():
                row.set_subtitle(_subtitle(result))
            elif button.get_root() is not None:
                row.set_subtitle("Revue révoquée. Relancer la mesure.")

        try:
            window.runner.submit(
                "Mesure du journal des variantes…",
                lambda: review_derived_capacity(service, request),
                done, lambda _exc: done(None), control=button,
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )
        except Exception:
            done(None)

    button.connect("clicked", clicked)
    return True
