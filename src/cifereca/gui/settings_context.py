from __future__ import annotations

from dataclasses import dataclass


PERSISTABLE_SETTINGS_TABS = frozenset({"accounts", "library", "sync", "interface", "ai"})
RECONNECT_SETTINGS_TABS = frozenset({"accounts", "sync"})


@dataclass(frozen=True)
class SettingsActionState:
    show_save: bool
    save_sensitive: bool
    show_reconnect: bool
    reconnect_sensitive: bool


def settings_action_state(tab: str, *, dirty: bool) -> SettingsActionState:
    current = str(tab or "")
    show_save = current in PERSISTABLE_SETTINGS_TABS
    show_reconnect = current in RECONNECT_SETTINGS_TABS
    return SettingsActionState(
        show_save=show_save,
        save_sensitive=show_save and bool(dirty),
        show_reconnect=show_reconnect,
        reconnect_sensitive=show_reconnect,
    )
