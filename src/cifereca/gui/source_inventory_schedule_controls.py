"""Generic Sync inventory/rights scheduling, separate from file delivery consent.

Core owns supported providers, authorization and schedule persistence. This
widget reads only a validated per-source policy; it never infers permission
from provider IDs or from the availability of public catalogue search (#3416).
"""
from __future__ import annotations

from typing import Any

from gi.repository import Gtk, Adw

from .sync_action_layout import add_sync_action, sync_action_row
from .sync_action_scope import sync_action_scope_current


def append_source_inventory_schedule_controls(
    window: Any, broker: Any, source: Any, scope: Any, group: Any,
) -> None:
    """Render an opt-in remote inventory schedule, not an acquisition control."""
    if (not sync_action_scope_current(window, scope)
            or getattr(source, "identity_id", None) != scope.identity_id
            or getattr(source, "sync_capable", None) is not True):
        return
    caps = getattr(source, "sync_capabilities", ())
    if (type(caps) not in (tuple, list)
            or len(caps) > 32 or "remote_inventory" not in caps
            or "scheduled_refresh" not in caps):
        return
    read = getattr(broker, "inventory_refresh_schedule", None)
    write = getattr(broker, "set_inventory_refresh_schedule", None)
    if not (callable(read) and callable(write)):
        return
    sid = source.source_id  # Opaque Core route, never visible as a label.
    try:
        policy = read(sid)
    except Exception:
        # Unsupported providers do not get an invented schedule switch.
        return
    if not sync_action_scope_current(window, scope):
        return
    if (type(policy) is not dict
            or set(policy) != {"mode", "interval_minutes"}
            or policy["mode"] not in {"manual", "on_start", "periodic"}
            or type(policy["interval_minutes"]) is not int
            or not 15 <= policy["interval_minutes"] <= 10080):
        return
    mode, interval = policy["mode"], policy["interval_minutes"]
    row = Adw.ActionRow()
    row.set_title("Actualisation automatique des notices et droits")
    row.set_subtitle_lines(0)
    if mode == "manual":
        row.set_subtitle(
            "Manuelle : les notices et droits ne sont pas actualisés "
            "automatiquement. Les téléchargements sont réglés séparément."
        )
    elif mode == "on_start":
        row.set_subtitle(
            f"Au démarrage (intervalle minimal {interval} min). "
            "N'autorise aucun téléchargement."
        )
    else:
        row.set_subtitle(
            f"Périodique (intervalle {interval} min, si ordonnanceur actif). "
            "N'autorise aucun téléchargement."
        )

    def current() -> bool:
        return (sync_action_scope_current(window, scope)
                and getattr(source, "identity_id", None) == scope.identity_id)

    def apply_mode(target: str) -> None:
        if not current():
            return
        if (target != "manual"
                and not (source.enabled is True and source.connected is True
                         and source.sync_capable is True)):
            return
        try:
            latest = read(sid)
            if (type(latest) is not dict
                    or latest.get("mode") != mode
                    or latest.get("interval_minutes") != interval):
                if current():
                    window._refresh_sync_page()
                return
            result = write(sid, target, interval_minutes=interval)
            if (type(result) is not dict
                    or result.get("mode") != target
                    or result.get("interval_minutes") != interval):
                raise ValueError("invalid schedule acknowledgement")
        except Exception:
            if current():
                window.show_error(
                    "Planification des notices indisponible. "
                    "Actualisez la page avant de réessayer.",
                    "Synchronisation",
                )
            return
        if current():
            window._refresh_sync_page()

    if mode == "manual":
        for target, label in (
            ("on_start", "Au démarrage…"),
            ("periodic", "Périodique…"),
        ):
            button = window.action_button(label, "preferences-system-symbolic")
            button.set_sensitive(bool(
                source.enabled is True and source.connected is True
            ))
            button.set_tooltip_text(
                "Autoriser les lectures distantes de notices et droits "
                "pour ce seul compte, sans acquisition de fichier."
            )
            def ask(_button, wanted=target):
                if not current() or source.enabled is not True or source.connected is not True:
                    return
                dialog = Adw.MessageDialog.new(
                    window, "Actualiser automatiquement les notices et droits ?",
                    "Autoriser les lectures du catalogue personnel et des droits "
                    "de ce compte. Aucun fichier EPUB/ACSM ne sera téléchargé "
                    "par ce réglage. Le consentement aux acquisitions est distinct.",
                )
                dialog.add_response("cancel", "Annuler")
                dialog.add_response("confirm", "Autoriser les actualisations")
                dialog.set_response_appearance(
                    "confirm", Adw.ResponseAppearance.SUGGESTED,
                )
                def respond(_dialog, choice):
                    if choice == "confirm" and current():
                        apply_mode(wanted)
                dialog.connect("response", respond)
                dialog.present()
            button.connect("clicked", ask)
            add_sync_action(row, button)
    else:
        button = window.action_button("Désactiver les actualisations",
                                      "media-playback-stop-symbolic")
        button.set_tooltip_text(
            "Revenir en mode manuel sans changer les permissions de téléchargement."
        )
        button.connect("clicked", lambda _button: apply_mode("manual"))
        add_sync_action(row, button)
    group.add(sync_action_row(row))
