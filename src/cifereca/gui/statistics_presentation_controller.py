"""Identity statistics presentation for the CoreUI shell (#1051).

Keep the service as the owner of counts and provenance. This module only
projects its existing snapshot into labels and a dialog; no GTK import,
provider calls, subscriptions, or implicit refresh are introduced.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def statistics_button_label(stats: Mapping[str, Any]) -> str:
    loans = stats["known_loans"]
    return (
        f"{stats['local_books']} livres · "
        f"{loans if loans is not None else '—'} prêts de bibliothèque"
    )


def statistics_lines(stats: Mapping[str, Any]) -> list[str]:
    """Preserve the existing ordering, zero counts and freshness caveats."""
    loans = stats["known_loans"]
    rows = [
        f"Livres locaux distincts : {stats['local_books']}",
        f"Emplacements locaux indexés présents : {stats['local_placements']}",
        f"Prêts de bibliothèque connus : {loans if loans is not None else 'non actualisés'}",
        f"Sources de prêts observées : {stats['loan_sources_observed']}",
        f"Droits d’achat distants actifs : {stats['remote_owned_rights']}",
    ]
    for title, key in (
        ("Formats", "formats"),
        ("Sources locales", "sources"),
        ("Couvertures EPUB", "epub_covers"),
    ):
        values = stats[key]
        rows.append(
            title + " : " +
            (", ".join(f"{name} {count}" for name, count in sorted(values.items())) or "—")
        )
    actions = stats.get("actions") or {}
    if actions:
        rows.append("Actions sur la période :")
        rows.append(
            f"  Acquisitions : {actions.get('acquisitions_succeeded', 0)} réussies · "
            f"{actions.get('acquisitions_failed', 0)} échouées"
        )
        rows.append(
            f"  Imports : {actions.get('imports_succeeded', 0)} réussis · "
            f"{actions.get('imports_failed', 0)} échoués"
        )
        rows.append(
            f"  Synchronisations : {actions.get('synchronizations_succeeded', 0)} réussies · "
            f"{actions.get('synchronizations_failed', 0)} échouées"
        )
        rows.append(
            f"  Transformations : {actions.get('transformations_succeeded', 0)} réussies · "
            f"{actions.get('transformations_failed', 0)} échouées"
        )
        rows.append(
            f"  Transferts : {actions.get('transfers_succeeded', 0)} réussis · "
            f"{actions.get('transfers_failed', 0)} échoués"
        )
        if actions.get("from_timestamp") and actions.get("to_timestamp"):
            first = datetime.fromtimestamp(actions["from_timestamp"]).strftime("%d/%m/%Y %H:%M")
            last = datetime.fromtimestamp(actions["to_timestamp"]).strftime("%d/%m/%Y %H:%M")
            rows.append(f"  Fenêtre : {first} → {last}")
    if stats.get("loans_observed_at"):
        observed = datetime.fromtimestamp(stats["loans_observed_at"]).strftime("%d/%m/%Y %H:%M")
        rows.append("Prêts observés au plus tôt : " + observed)
    rows.append(
        "Inventaire des fichiers issu du dernier scan ; prêts de bibliothèque issus des dernières réponses, "
        "éventuellement partielles. Les livres achetés en ligne ne sont pas des emprunts ni des fichiers locaux. "
        "Les statistiques d'actions utilisent la fenêtre indiquée."
    )
    return rows


def refresh_statistics(window) -> None:
    """Refresh only the preexisting shell button; never start data work."""
    if getattr(window, "_closed", False) or not hasattr(window, "statistics_button"):
        return
    identity = window.service.current_identity
    if not identity:
        return
    stats = window.service.identity_statistics(identity.identity_id)
    if not getattr(window, "_closed", False):
        window.statistics_button.set_label(statistics_button_label(stats))


def show_statistics(window, *, Adw) -> None:
    """Display the active identity's snapshot with the same native dialog."""
    if getattr(window, "_closed", False):
        return
    identity = window.service.current_identity
    if not identity:
        return
    stats = window.service.identity_statistics(identity.identity_id)
    if getattr(window, "_closed", False):
        return
    dialog = Adw.MessageDialog.new(
        window,
        f"Statistiques · {identity.label}",
        "\n".join(statistics_lines(stats)),
    )
    dialog.add_response("close", "Fermer")
    dialog.present()
