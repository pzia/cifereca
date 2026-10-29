"""#3171: optional account-local, explicitly confirmed canonical acquisition grants.

Never render provider IDs, opaque right IDs, credentials, signed URLs or paths.
Only the Core broker resolves the opaque reference against attested SQLite A.
No provider I/O occurs until a separate, later authorized Sync.
"""
from __future__ import annotations

from typing import Any

from gi.repository import Adw, Gtk

from .sync_action_layout import add_sync_action
from .sync_action_scope import sync_action_scope_current
from .sync_source_details import add_sync_detail


def append_canonical_consent_controls(
    window: Any, broker: Any, source: Any, scope: Any, group: Any,
) -> int:
    read = getattr(broker, "canonical_acquisition_choices", None)
    mutate = getattr(broker, "set_canonical_acquisition_choice", None)
    if not (callable(read) and callable(mutate)
            and getattr(source, "enabled", None) is True
            and sync_action_scope_current(window, scope)):
        return 0
    try:
        choices = read(source.source_id)
    except Exception:
        return 0
    if type(choices) is not tuple or len(choices) > 48:
        return 0
    # Reject an incomplete/malformed broker response rather than displaying
    # a toggle which could target a different right than the user saw.
    safe = []
    seen = set()
    for choice in choices:
        if (type(choice) is not dict
                or set(choice) != {"reference", "kind", "allowed", "eligible"}
                or type(choice["reference"]) is not str
                or len(choice["reference"]) != 64
                or any(c not in "0123456789abcdef" for c in choice["reference"])
                or choice["reference"] in seen
                or choice["kind"] not in ("purchase", "loan")
                or type(choice["allowed"]) is not bool
                or type(choice["eligible"]) is not bool):
            return 0
        seen.add(choice["reference"])
        safe.append(choice)
    if not sync_action_scope_current(window, scope):
        return 0

    for number, choice in enumerate(safe, 1):
        allowed, eligible = choice["allowed"], choice["eligible"]
        kind = "Achat" if choice["kind"] == "purchase" else "Prêt"
        row = Adw.ActionRow()
        row.set_title(f"Acquisition canonique · {kind} n°{number}")
        row.set_subtitle(
            ("Autorisation active : révocable."
             if allowed else
             "Référence fournisseur positive vérifiée ; aucun téléchargement autorisé."
             if eligible else
             "Droit non actif ou non attesté ; autorisation indisponible.")
            + " Le droit et l’offre sont recontrôlés avant chaque téléchargement."
        )
        row.set_subtitle_lines(0)
        button = window.action_button(
            "Révoquer" if allowed else "Autoriser ce droit…",
            "edit-delete-symbolic" if allowed else "document-save-symbolic",
        )
        button.set_sensitive(bool(allowed or eligible))
        button.set_valign(Gtk.Align.CENTER)
        add_sync_action(row, button)

        def clicked(_button, *, ref=choice["reference"], previous=allowed,
                    sid=source.source_id, captured=scope):
            if not sync_action_scope_current(window, captured):
                return
            wanted = not previous
            dialog = Adw.MessageDialog(
                transient_for=window, modal=True,
                heading=("Révoquer ce téléchargement automatique ?"
                         if previous else
                         "Autoriser les téléchargements de ce droit ?"),
                body=(
                    "Cette action modifie uniquement l’autorisation persistante "
                    "du droit exact de ce compte. Révoquer n’efface aucun fichier "
                    "ni droit historique. La synchronisation suivante pourra "
                    "télécharger seulement si la session, le droit et l’offre "
                    "sont encore valides."
                ),
            )
            dialog.add_response("cancel", "Annuler")
            dialog.add_response("confirm", "Révoquer" if previous else "Autoriser")
            dialog.set_response_appearance(
                "confirm", Adw.ResponseAppearance.DESTRUCTIVE
                if previous else Adw.ResponseAppearance.SUGGESTED,
            )

            def answered(_dialog, response):
                if response != "confirm" or not sync_action_scope_current(window, captured):
                    return
                # Single exact-right broker mutation. No provider call, no
                # implicit opt-in for other accounts or upcoming discoveries.
                try:
                    mutate(sid, ref, allowed=wanted)
                except Exception:
                    if sync_action_scope_current(window, captured):
                        window.show_error(
                            "Autorisation canonique indisponible : vérifier ce droit.",
                            "Synchronisation",
                        )
                    return
                if sync_action_scope_current(window, captured):
                    window._refresh_sync_page()

            dialog.connect("response", answered)
            dialog.present()

        button.connect("clicked", clicked)
        add_sync_detail(group, row)
    return len(safe)
