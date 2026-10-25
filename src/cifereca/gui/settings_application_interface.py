"""Presentation-only GTK builders for Settings Application and Interface (#1127).

GTK and Adw are injected by MainWindow. No PyGObject is imported here.
The caller retains signal wiring, immediate diagnostics persistence, dirty state,
navigation, lifecycle, and async settings commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApplicationInterfaceWidgets:
    diagnostics_switch: Any | None
    experimental_switch: Any
    level_dropdown: Any
    levels: list[str]
    plugins_button: Any


def build_settings_application_interface(
    *, diagnostic_section: Any, interface_section: Any,
    config: Any, Gtk: Any, Adw: Any,
    effective_diagnostics_level: str | None = None,
) -> ApplicationInterfaceWidgets:
    """Build the original controls and parent hierarchy without side effects."""
    config_group = Adw.PreferencesGroup()
    config_group.set_title("Application")
    diagnostic_section.append(config_group)

    exp_row = Adw.ActionRow()
    exp_row.set_title("Actions expérimentales")
    exp_row.set_subtitle("Prolongation et retour anticipé numérique")
    exp_switch = Gtk.Switch()
    exp_switch.set_active(bool(config.get("experimental_actions", False)))
    exp_switch.set_valign(Gtk.Align.CENTER)
    exp_row.add_suffix(exp_switch)
    interface_group = Adw.PreferencesGroup()
    interface_group.set_title("Interface et actions")
    interface_group.add(exp_row)
    interface_section.append(interface_group)
    interface_button = Gtk.Button(label="Configurer la barre de titre et les actions…")
    interface_section.append(interface_button)

    level_row = Adw.ActionRow()
    level_row.set_title("Niveau de diagnostic")
    level_row.set_subtitle("Appliqué et enregistré dès modification")
    levels = ["errors", "normal", "verbose", "trace"]
    level_drop = Gtk.DropDown.new_from_strings(levels)
    diagnostic_level = str(
        effective_diagnostics_level
        if effective_diagnostics_level is not None
        else config.get("diagnostics.level", "normal")
    )
    try:
        level_drop.set_selected(levels.index(
            {"detailed": "verbose"}.get(diagnostic_level, diagnostic_level)
        ))
    except ValueError:
        level_drop.set_selected(1)
    level_row.add_suffix(level_drop)
    config_group.add(level_row)

    return ApplicationInterfaceWidgets(
        diagnostics_switch=None,
        experimental_switch=exp_switch,
        level_dropdown=level_drop,
        levels=levels,
        plugins_button=interface_button,
    )
