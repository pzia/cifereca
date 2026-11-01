"""Active provider-neutral Settings Sync contract (#3732 / #3731).

Older Kobo-specific builder assertions are preserved separately in
test_settings_legacy_kobo_section_contract.py, not mistaken for live CoreUI.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.settings_lifetime import SettingsPageLifetime
from cifereca.gui.settings_model import SettingsRevisionState
from cifereca.gui.settings_neutral_sync_section import build_sync_settings_section
from cifereca.gui.settings_sync_actions import bind_settings_sync_actions


class Widget:
    def __init__(self, *, label=None):
        self.label = label
        self.title = ""
        self.subtitle = ""
        self.text = ""
        self.sensitive = True
        self.children = []
        self.suffixes = []
        self.handlers = {}
    def set_title(self, value): self.title = value
    def set_subtitle(self, value): self.subtitle = value
    def set_text(self, value): self.text = value
    def set_sensitive(self, value): self.sensitive = value
    def set_valign(self, _value): pass
    def append(self, child): self.children.append(child)
    def add(self, child): self.children.append(child)
    def add_suffix(self, child): self.suffixes.append(child)
    def connect(self, event, callback): self.handlers[event] = callback
    def click(self):
        if self.sensitive: self.handlers["clicked"](self)


def _build(config):
    section, downloads = Widget(), Widget()
    reads = []
    data = dict(config)
    def read(key, default=None):
        reads.append(key)
        return data.get(key, default)
    controls = build_sync_settings_section(
        section=section, download_group=downloads,
        config=NS(get=read), Gtk=NS(Button=Widget, Align=NS(CENTER=1)),
        Adw=NS(PreferencesGroup=Widget, ActionRow=Widget, EntryRow=Widget),
    )
    return controls, section, downloads, reads


def test_active_settings_sync_page_is_vendor_neutral_even_with_old_consent_on():
    controls, section, downloads, reads = _build({
        "library.directory": "/tmp/books",
        "kobo_backup.enabled": True,
        "kobo_backup.allow_unlinked_acquisition": True,
        "kobo_backup.interval_minutes": "corrupt",
    })
    assert reads == ["library.directory"]
    group, = section.children
    row, = group.children
    assert group.title == "Synchronisation par source"
    assert row.title == "Synchronisation — sources et autorisations"
    assert "Aucun consentement" in row.subtitle
    assert controls.open_sync_button in row.suffixes
    assert not hasattr(controls, "run_backup_now")
    assert not hasattr(controls, "backup_status")
    assert controls.library_dir_row in downloads.children
    assert controls.library_dir_row.text == "/tmp/books"
    assert not any("Kobo" in child.title for child in group.children)
    assert not hasattr(controls, "backup_switch")
    assert not hasattr(controls, "vault_profile_drop")


def test_navigation_requires_live_page_and_never_runs_global_acquisition():
    controls, _section, _downloads, _reads = _build({})
    calls = []
    host = NS(_closed=False, navigate=lambda route: calls.append(route))
    lifetime = SettingsPageLifetime()
    state = SettingsRevisionState(["sync"])
    refresh = bind_settings_sync_actions(
        host, page_lifetime=lifetime,
        settings_state=state, sync_settings=controls,
    )
    controls.open_sync_button.click()
    assert calls == ["sync"]
    state.mark_dirty("sync")
    refresh()
    assert controls.open_sync_button.sensitive is False
    assert "enregistrer" in controls.sync_navigation_status.subtitle
    controls.open_sync_button.click()
    assert calls == ["sync"]
    state.commit(state.snapshot())
    refresh()
    assert controls.open_sync_button.sensitive
    lifetime.dispose()
    controls.open_sync_button.click()
    assert calls == ["sync"]


def test_unknown_provider_requires_no_settings_special_case_or_network():
    controls, section, _downloads, reads = _build({
        "providers.accounts": [{
            "provider": "fictional.future", "enabled": False,
        }],
    })
    assert reads == ["library.directory"]
    row, = section.children[0].children
    assert "Synchronisation" in row.title
    assert not hasattr(controls, "backup_interval_row")
    assert not hasattr(controls, "backup_dir_row")
    assert row.suffixes == [controls.open_sync_button]
