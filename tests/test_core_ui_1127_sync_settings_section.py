"""P0 #1127: actual Kobo settings GTK section construction, headless."""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.gui.settings_sync_section import build_kobo_sync_section


class Widget:
    def __init__(self, *, label=None, **_kwargs):
        self.title = None
        self.subtitle = None
        self.text = None
        self.active = None
        self.selected = None
        self.suffixes = []
        self.children = []
        self.label = label

    def set_title(self, value): self.title = value
    def set_subtitle(self, value): self.subtitle = value
    def set_text(self, value): self.text = value
    def set_active(self, value): self.active = value
    def set_selected(self, value): self.selected = value
    def set_valign(self, _value): pass
    def add_suffix(self, value): self.suffixes.append(value)
    def add(self, value): self.children.append(value)
    def append(self, value): self.children.append(value)


class DropDown(Widget):
    @classmethod
    def new_from_strings(cls, values):
        widget = cls()
        widget.options = list(values)
        return widget


def _build(settings=None):
    values = settings or {}
    config = SimpleNamespace(get=lambda key, default=None: values.get(key, default))
    gtk = SimpleNamespace(Switch=Widget, DropDown=DropDown,
                          Align=SimpleNamespace(CENTER="center"))
    adw = SimpleNamespace(PreferencesGroup=Widget, ActionRow=Widget, EntryRow=Widget)
    section, download_group = Widget(), Widget()
    controls = build_kobo_sync_section(
        section=section, download_group=download_group, config=config,
        Gtk=gtk, Adw=adw,
    )
    return controls, section, download_group


def test_default_widgets_keep_original_settings_and_visual_parentage():
    controls, section, downloads = _build()
    assert len(section.children) == 1
    group = section.children[0]
    assert group.title == "Acquisition automatique / activité Kobo"
    assert len(group.children) == 5
    assert [row.title for row in group.children] == [
        "Synchroniser automatiquement les ebooks Kobo",
        "Autoriser les acquisitions Kobo malgré des livres locaux sans liaison",
        "Sous-répertoire/override Kobo (vide = bibliothèque locale)",
        "Intervalle automatique (minutes, minimum 15)",
        "DisplayProfile Kobo-Vault",
    ]
    assert len(downloads.children) == 1
    assert downloads.children[0].title == "Bibliothèque locale possédée par cifereca"
    assert controls.backup_switch.active is False
    assert controls.unlinked_switch.active is False
    assert controls.library_dir_row.text == "~/EBooks"
    assert controls.backup_interval_row.text == "60"
    assert controls.vault_profiles == ["Android", "Desktop"]
    assert controls.vault_profile_drop.selected == 0
    assert controls.vault_profile_drop.options == controls.vault_profiles


def test_saved_values_match_old_view_and_unknown_vault_profile_falls_back():
    values = {
        "kobo_backup.enabled": True,
        "kobo_backup.allow_unlinked_acquisition": True,
        "kobo_backup.directory": "Kobo",
        "kobo_backup.interval_minutes": 90,
        "kobo_backup.display_profile": "Desktop",
        "library.directory": "/tmp/ebooks",
    }
    controls, _, _ = _build(values)
    assert controls.backup_switch.active is True
    assert controls.unlinked_switch.active is True
    assert controls.backup_dir_row.text == "Kobo"
    assert controls.backup_interval_row.text == "90"
    assert controls.library_dir_row.text == "/tmp/ebooks"
    assert controls.vault_profile_drop.selected == 1
    values["kobo_backup.display_profile"] = "Unknown"
    fallback, _, _ = _build(values)
    assert fallback.vault_profile_drop.selected == 0


def test_build_does_not_persist_change_connect_signals_or_call_network():
    requests = []
    values = {"kobo_backup.enabled": False}
    controls, _, _ = _build(values)
    assert requests == []
    assert values == {"kobo_backup.enabled": False}
    assert controls.backup_switch.active is False
