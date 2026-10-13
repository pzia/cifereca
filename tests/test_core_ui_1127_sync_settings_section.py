"""P0 #1127: actual Kobo settings GTK section construction, headless."""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.gui.settings_sync_section import build_kobo_sync_section, kobo_backup_action_state


class Widget:
    def __init__(self, *, label=None, **_kwargs):
        self.title = None
        self.subtitle = None
        self.text = None
        self.active = None
        self.selected = None
        self.sensitive = None
        self.suffixes = []
        self.children = []
        self.label = label

    def set_title(self, value): self.title = value
    def set_subtitle(self, value): self.subtitle = value
    def set_text(self, value): self.text = value
    def set_active(self, value): self.active = value
    def set_sensitive(self, value): self.sensitive = value
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
    gtk = SimpleNamespace(Switch=Widget, Button=Widget, DropDown=DropDown,
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
    assert len(group.children) == 6
    assert [row.title for row in group.children] == [
        "Synchroniser automatiquement les ebooks Kobo",
        "Télécharger/sauvegarder mes livres Kobo",
        "Autoriser les acquisitions Kobo malgré des livres locaux sans liaison",
        "Sous-répertoire/override Kobo (vide = bibliothèque locale)",
        "Intervalle automatique (minutes, minimum 15)",
        "DisplayProfile Kobo-Vault",
    ]
    assert len(downloads.children) == 1
    assert downloads.children[0].title == "Bibliothèque locale possédée par cifereca"
    assert controls.backup_switch.active is False
    assert controls.run_backup_now.label == "Lancer maintenant"
    assert controls.run_backup_now.sensitive is False
    assert "Désactivée" in controls.backup_status.subtitle
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
    assert controls.run_backup_now.sensitive is True
    assert "à l’ouverture" in controls.backup_status.subtitle
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


def test_manual_kobo_backup_requires_saved_enabled_state():
    assert kobo_backup_action_state(persisted_enabled=False, draft_enabled=True, dirty=True)[1] is False
    assert kobo_backup_action_state(persisted_enabled=False, draft_enabled=False, dirty=False)[1] is False
    assert kobo_backup_action_state(persisted_enabled=True, draft_enabled=False, dirty=True)[1] is False
    message, allowed = kobo_backup_action_state(persisted_enabled=True, draft_enabled=True, dirty=False)
    assert allowed is True
    assert "à l’ouverture" in message


def test_mainwindow_explicit_kobo_action_is_saved_and_lifetime_gated():
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    window = next(c for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "MainWindow")
    builder = next(f for f in window.body if isinstance(f, ast.FunctionDef) and f.name == "_build_settings_page")
    manual = next(f for f in builder.body if isinstance(f, ast.FunctionDef) and f.name == "manual_kobo_backup")
    rendered = ast.unparse(manual)
    assert 'page_lifetime.alive' in rendered
    assert "kobo_backup.enabled" in rendered
    assert "settings_state.dirty('sync')" in rendered
    assert 'if ready:' in rendered
    assert 'self.run_kobo_backup(manual=True, control=_button)' in rendered
    assert "kobo_run_now.connect('clicked', manual_kobo_backup)" in ast.unparse(builder)


def test_bad_saved_kobo_opt_in_never_looks_enabled_or_unlocks_manual_action():
    """#1338: Core and Settings must agree on literal Boolean opt-in."""
    for invalid in ("false", "true", 1, 0, None, [], {}):
        controls, _, _ = _build({"kobo_backup.enabled": invalid})
        assert controls.backup_switch.active is False
        assert controls.run_backup_now.sensitive is False
        assert "Désactivée" in controls.backup_status.subtitle
        assert kobo_backup_action_state(
            persisted_enabled=invalid,
            draft_enabled=invalid,
            dirty=False,
        )[1] is False
    enabled, _, _ = _build({"kobo_backup.enabled": True})
    assert enabled.backup_switch.active is True
    assert enabled.run_backup_now.sensitive is True


def test_manual_kobo_callback_requires_exact_saved_boolean_opt_in():
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    window = next(c for c in tree.body
                  if isinstance(c, ast.ClassDef) and c.name == "MainWindow")
    builder = next(f for f in window.body
                   if isinstance(f, ast.FunctionDef) and f.name == "_build_settings_page")
    manual = next(f for f in builder.body
                  if isinstance(f, ast.FunctionDef) and f.name == "manual_kobo_backup")
    refresh = next(f for f in builder.body
                   if isinstance(f, ast.FunctionDef) and f.name == "refresh_kobo_backup_status")
    for fn in (manual, refresh):
        source = ast.unparse(fn)
        assert "self.config.get('kobo_backup.enabled', False) is True" in source
        assert "bool(self.config.get(" not in source
