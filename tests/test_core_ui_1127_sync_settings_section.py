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
    assert group.title == "Kobo — acquisition locale des fichiers"
    assert len(group.children) == 6
    assert [row.title for row in group.children] == [
        "Ancien réglage global Kobo (compatibilité)",
        "Synchronisation Kobo — comptes et aperçus",
        "Ancienne dérogation Kobo (compatibilité)",
        "Sous-répertoire/override Kobo (vide = bibliothèque locale)",
        "Délai entre tentatives au démarrage (minutes, minimum 15)",
        "DisplayProfile Kobo-Vault",
    ]
    assert len(downloads.children) == 1
    assert downloads.children[0].title == "Bibliothèque locale possédée par cifereca"
    assert controls.backup_switch.active is False
    assert controls.backup_switch.sensitive is False
    assert controls.unlinked_switch.sensitive is False
    assert controls.run_backup_now.label == "Examiner les comptes Kobo…"
    assert controls.run_backup_now.sensitive is True
    assert "Ancien réglage global désactivé" in controls.backup_status.subtitle
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
    assert controls.backup_switch.sensitive is False
    assert controls.unlinked_switch.sensitive is False
    assert controls.run_backup_now.sensitive is True
    assert "sans effet sur l’acquisition" in controls.backup_status.subtitle
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


def test_kobo_account_navigation_requires_saved_settings_not_acquisition_opt_in():
    assert kobo_backup_action_state(persisted_enabled=False, draft_enabled=True, dirty=True)[1] is False
    assert kobo_backup_action_state(persisted_enabled=False, draft_enabled=False, dirty=False)[1] is True
    assert kobo_backup_action_state(persisted_enabled=True, draft_enabled=False, dirty=True)[1] is False
    message, allowed = kobo_backup_action_state(persisted_enabled=True, draft_enabled=True, dirty=False)
    assert allowed is True
    assert "sans effet sur l’acquisition" in message


def test_mainwindow_kobo_action_is_navigation_only_saved_and_lifetime_gated():
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/settings_sync_actions.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    builder = next(f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == "bind_settings_sync_actions")
    manual = next(f for f in builder.body if isinstance(f, ast.FunctionDef) and f.name == "open_kobo_sync_page")
    rendered = ast.unparse(manual)
    assert 'page_lifetime.alive' in rendered
    assert "kobo_backup.enabled" in rendered
    assert "settings_state.dirty('sync')" in rendered
    assert 'if ready and page_lifetime.alive' in rendered
    assert "self.navigate('sync')" in rendered
    assert "run_kobo_backup" not in rendered
    assert "kobo_run_now.connect('clicked', open_kobo_sync_page)" in ast.unparse(builder)


def test_malformed_backup_opt_in_does_not_activate_automatic_acquisition():
    """#1338: Core and Settings must agree on literal Boolean opt-in."""
    for invalid in ("false", "true", 1, 0, None, [], {}):
        controls, _, _ = _build({"kobo_backup.enabled": invalid})
        assert controls.backup_switch.active is False
        assert controls.run_backup_now.sensitive is True
        assert "Ancien réglage global désactivé" in controls.backup_status.subtitle
        assert kobo_backup_action_state(
            persisted_enabled=invalid,
            draft_enabled=invalid,
            dirty=False,
        )[1] is False
    enabled, _, _ = _build({"kobo_backup.enabled": True})
    assert enabled.backup_switch.active is True
    assert enabled.run_backup_now.sensitive is True


def test_kobo_navigation_callback_reads_saved_boolean_without_implicit_opt_in():
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/settings_sync_actions.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    builder = next(f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == "bind_settings_sync_actions")
    manual = next(f for f in builder.body
                  if isinstance(f, ast.FunctionDef) and f.name == "open_kobo_sync_page")
    refresh = next(f for f in builder.body
                   if isinstance(f, ast.FunctionDef) and f.name == "refresh_kobo_backup_status")
    for fn in (manual, refresh):
        source = ast.unparse(fn)
        assert "self.config.get('kobo_backup.enabled', False) is True" in source
        assert "bool(self.config.get(" not in source


def test_sync_settings_never_mislabel_local_acquisition_as_remote_inventory_refresh():
    _, section, _ = _build({"kobo_backup.enabled": True})
    group = section.children[0]
    automatic, manual = group.children[:2]
    assert "Ancien réglage global Kobo" in automatic.title
    assert "ne lance plus de téléchargements" in automatic.subtitle
    assert "par compte dans Synchronisation" in automatic.subtitle
    assert "comptes et aperçus" in manual.title
    assert "sans effet sur l’acquisition" in manual.subtitle
    assert "Seule l’autorisation explicite" in manual.subtitle
    assert "dans Synchronisation" in manual.subtitle


def test_opening_interval_is_not_described_as_periodic_timer():
    _, section, _ = _build()
    group = section.children[0]
    interval = group.children[4]
    assert "au démarrage" in interval.title
    assert "minuterie périodique" in group.children[0].subtitle


def test_unlinked_option_explicitly_warns_about_duplicate_downloads():
    _, section, _ = _build()
    risky = section.children[0].children[2]
    assert "Ancienne dérogation Kobo" in risky.title
    assert "ne contourne ni la vérification des droits" in risky.subtitle
    assert "anti-doublons" in risky.subtitle


def test_sync_setting_builder_has_no_implicit_acquisition_or_config_mutation():
    values = {"kobo_backup.enabled": True, "kobo_backup.allow_unlinked_acquisition": False}
    before = dict(values)
    controls, section, _ = _build(values)
    assert values == before
    assert controls.backup_switch.active is True
    assert controls.unlinked_switch.active is False
    assert controls.run_backup_now.sensitive is True
    assert controls.run_backup_now.label == "Examiner les comptes Kobo…"


def test_malformed_unlinked_bypass_config_is_disabled_like_core_policy():
    """Core checks `is True`, not bool(value); GTK must use the same gate."""
    for invalid in ("false", "true", 1, 0, [], {}, None):
        controls, _, _ = _build({
            "kobo_backup.enabled": True,
            "kobo_backup.allow_unlinked_acquisition": invalid,
        })
        assert controls.unlinked_switch.active is False
    valid, _, _ = _build({
        "kobo_backup.allow_unlinked_acquisition": True,
    })
    assert valid.unlinked_switch.active is True


def test_legacy_kobo_switch_cannot_claim_authorization_by_itself():
    controls, section, _ = _build({"kobo_backup.enabled": True})
    group = section.children[0]
    assert "ne lance plus" in group.children[0].subtitle
    assert "compte" in group.children[0].subtitle
    assert "sans effet sur l’acquisition" in controls.backup_status.subtitle
    assert "anti-doublons" in group.children[2].subtitle
    assert "contourne" in group.children[2].subtitle
    # A candidate of uncertain presence no longer acts as a veto against
    # delivery under the canonical Sync contract; legacy settings grant none.
    assert "présence locale incertaine ne bloque pas" in group.children[2].subtitle
    assert "seul le consentement de la source" in group.children[2].subtitle
    assert "rapprochement sûr avant" not in group.children[2].subtitle


def test_obsolete_global_kobo_switches_remain_inert_read_only_with_saved_values():
    values = {
        "kobo_backup.enabled": True,
        "kobo_backup.allow_unlinked_acquisition": True,
    }
    before = dict(values)
    controls, section, _ = _build(values)
    assert values == before
    assert controls.backup_switch.active is True
    assert controls.unlinked_switch.active is True
    assert controls.backup_switch.sensitive is False
    assert controls.unlinked_switch.sensitive is False
    assert "lecture seule" in section.children[0].children[0].subtitle
    assert "lecture seule" in section.children[0].children[2].subtitle
    # Navigation to per-source consent remains a real, independent action.
    assert controls.run_backup_now.sensitive is True
