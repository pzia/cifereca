"""P1 #3731: active Settings is provider-neutral; legacy keys are opaque."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.settings_neutral_sync_section import (
    build_sync_settings_section,
)


class Widget:
    def __init__(self, *, label=None):
        self.label = label
        self.children = []
        self.suffixes = []
        self.title = ""
        self.subtitle = ""
        self.text = ""
        self.sensitive = True
    def append(self, child): self.children.append(child)
    def add(self, child): self.children.append(child)
    def add_suffix(self, child): self.suffixes.append(child)
    def set_title(self, value): self.title = value
    def set_subtitle(self, value): self.subtitle = value
    def set_text(self, value): self.text = value
    def set_sensitive(self, value): self.sensitive = value
    def set_valign(self, value): pass


def test_active_sync_settings_contains_only_neutral_navigation_and_library_directory():
    section, downloads = Widget(), Widget()
    reads = []
    def config_get(key, default=None):
        reads.append(key)
        if key == "library.directory": return "/tmp/synthetic-library"
        raise AssertionError("No provider preference may be read in neutral Settings")
    controls = build_sync_settings_section(
        section=section, download_group=downloads,
        config=NS(get=config_get), Gtk=NS(Button=Widget, Align=NS(CENTER=1)),
        Adw=NS(PreferencesGroup=Widget, ActionRow=Widget, EntryRow=Widget),
    )
    assert reads == ["library.directory"]
    assert len(section.children) == 1
    group, = section.children
    assert group.title == "Synchronisation par source"
    row, = group.children
    assert row.title == "Synchronisation — sources et autorisations"
    assert "Aucun consentement" in row.subtitle
    assert row.suffixes == [controls.open_sync_button]
    assert controls.open_sync_button is controls.open_sync_button
    assert controls.sync_navigation_status is controls.sync_navigation_status
    assert controls.open_sync_button.label == "Ouvrir Synchronisation…"
    assert controls.sync_navigation_status is row
    assert len(downloads.children) == 1
    assert controls.library_dir_row in downloads.children
    assert controls.library_dir_row.text == "/tmp/synthetic-library"
    assert not hasattr(controls, "backup_switch")
    assert not hasattr(controls, "backup_dir_row")
    assert not hasattr(controls, "backup_interval_row")
    assert not hasattr(controls, "vault_profile_drop")
    assert not hasattr(controls, "vault_profiles")


def test_retired_provider_configuration_cannot_drive_neutral_settings():
    values = {
        "library.directory": "~/EBooks",
        "kobo_backup.enabled": True, "kobo_backup.allow_unlinked_acquisition": True,
        "kobo_backup.directory": "private",
        "kobo_backup.interval_minutes": "invalid",
        "kobo_backup.display_profile": "other",
    }
    keys = []
    def config_get(key, default=None):
        keys.append(key)
        return values.get(key, default)
    section, downloads = Widget(), Widget()
    build_sync_settings_section(
        section=section, download_group=downloads,
        config=NS(get=config_get), Gtk=NS(Button=Widget, Align=NS(CENTER=1)),
        Adw=NS(PreferencesGroup=Widget, ActionRow=Widget, EntryRow=Widget),
    )
    assert keys == ["library.directory"]
    assert len(section.children[0].children) == 1
    assert "Kobo" not in str(section.children[0].title)
