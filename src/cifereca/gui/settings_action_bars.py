"""Settings action bars GTK builder (#1127).

Only constructs and mirrors widgets; MainWindow retains dirty tracking,
save/reconnect callbacks and lifecycle ownership. No Gtk import at module load.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SettingsActionBars:
    save: Any
    reconnect: Any
    save_bottom: Any
    reconnect_bottom: Any
    heading_bar: Any
    footer_bar: Any


def build_settings_action_bars(
    *, root: Any, heading: Any, adaptive_action_bar: Any,
    action_button: Any, mirror_sensitivity: Any, Gtk: Any,
) -> SettingsActionBars:
    header = adaptive_action_bar(source=heading)
    header.set_halign(Gtk.Align.END)
    heading.append(header)
    save = action_button(
        "Enregistrer", "document-save-symbolic", suggested=True,
        tooltip="Enregistrer les modifications de configuration en attente",
    )
    reconnect = action_button(
        "Reconnecter", "network-transmit-receive-symbolic",
        tooltip="Enregistrer les modifications en attente puis reconnecter les sources",
    )
    header.append(save)
    header.append(reconnect)

    footer = adaptive_action_bar(source=root)
    footer.set_halign(Gtk.Align.END)
    save_bottom = action_button(
        "Enregistrer", "document-save-symbolic", suggested=True,
        tooltip="Enregistrer les modifications de configuration en attente",
    )
    reconnect_bottom = action_button(
        "Reconnecter", "network-transmit-receive-symbolic",
        tooltip="Enregistrer les modifications en attente puis reconnecter les sources",
    )
    footer.append(save_bottom)
    footer.append(reconnect_bottom)
    root.append(footer)
    mirror_sensitivity(save, save_bottom)
    mirror_sensitivity(reconnect, reconnect_bottom)
    return SettingsActionBars(
        save, reconnect, save_bottom, reconnect_bottom, header, footer,
    )
