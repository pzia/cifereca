"""Exercise real Settings controller ports and assembly without importing GTK."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui import settings_page, settings_editor_actions, settings_diagnostics_actions
from cifereca.gui.settings_lifetime import SettingsPageLifetime
from cifereca.gui.settings_model import SettingsRevisionState
from cifereca.gui.settings_sync_actions import bind_settings_sync_actions


class Widget:
    def __init__(self, text='', active=False, **kwargs):
        self.text, self.active = text, active
        self.signals, self.children = {}, []
        self.visible, self.sensitive = True, True

    def connect(self, signal, callback):
        self.signals.setdefault(signal, []).append(callback)

    def emit(self, signal):
        for callback in self.signals.get(signal, []):
            callback(self)

    def append(self, child): self.children.append(child)
    def get_text(self): return self.text
    def get_active(self): return self.active
    def set_visible(self, visible): self.visible = visible
    def set_sensitive(self, sensitive): self.sensitive = sensitive
    def set_subtitle(self, text): self.subtitle = text
    def set_label(self, label): self.label = label
    def set_tooltip_text(self, text): self.tooltip = text


@pytest.mark.parametrize('standalone', [False, True])
def test_composition_disposes_only_same_surface_and_wires_all_save_buttons(monkeypatch, standalone):
    host = NS(
        _settings_main_lifetime=SettingsPageLifetime(),
        _settings_standalone_lifetime=SettingsPageLifetime(),
        _settings_tabs=object(), _diag_refresh=object(),
        service=NS(provider_descriptors=lambda: []), config={},
        diagnostics=NS(level='normal'),
        navigate=Mock(), action_button=Mock(),
    )
    previous_main, previous_standalone = host._settings_main_lifetime, host._settings_standalone_lifetime
    old_tabs, old_refresh = host._settings_tabs, host._diag_refresh
    events = []
    shell = NS(scroll=object(), root=Widget(), heading=Widget(), tabs=Widget(),
               sections={key: Widget() for key in ('accounts', 'library', 'sync', 'interface', 'ai', 'diagnostic')})
    bars = NS(**{key: Widget() for key in ('save', 'save_bottom', 'reconnect', 'reconnect_bottom', 'heading_bar', 'footer_bar')})
    kobo = NS(library_dir_row=Widget('/initial'))
    actions = {}
    capture_directory = []
    def editors(window, **kwargs):
        capture_directory.append(kwargs['library_directory'])
        return [], []
    def tracking(**kwargs):
        return Mock(), Mock(), lambda: events.append('track'), actions
    def sync(window, **kwargs):
        assert events == ['track']
        assert kwargs['kobo_settings'] is kobo
        return Mock()
    save, reconnect, refresh = Mock(return_value=True), Mock(), Mock()
    def commit(window, **kwargs):
        assert kwargs['refresh_text'] is refresh
        assert kwargs['settings_bars'] is bars
        assert kwargs['identity_editors'] == []
        assert kwargs['page_lifetime'].alive
        return save, reconnect
    patches = {
        'build_settings_shell': lambda **kwargs: shell,
        'bind_settings_actions': tracking, 'build_settings_editors': editors,
        'build_library_download_section': lambda **kwargs: NS(download_group=Widget()),
        'build_settings_application_interface': lambda **kwargs: NS(plugins_button=Widget()),
        'build_kobo_sync_section': lambda **kwargs: kobo,
        'build_settings_action_bars': lambda **kwargs: bars,
        'build_ai_settings_section': lambda **kwargs: NS(),
        'bind_settings_ai_callbacks': lambda *args, **kwargs: Mock(),
        'bind_settings_sync_actions': sync,
        'build_settings_diagnostics_section': lambda **kwargs: NS(),
        'bind_settings_diagnostics': lambda *args, **kwargs: refresh,
        'bind_settings_commit': commit,
    }
    for name, fn in patches.items(): monkeypatch.setattr(settings_page, name, fn)
    page = settings_page.build_settings_page(
        host, standalone=standalone, Gtk=NS(), Adw=NS(), Gdk=NS(), TaskPriority=NS(),
        AdaptiveActionBar=Mock(), action_button=Mock(), add_css=Mock(), clear_box=Mock(), mirror_sensitivity=Mock(),
    )
    assert page.scroll is shell.scroll
    assert previous_main.alive is standalone
    assert previous_standalone.alive is not standalone
    assert host._settings_tabs is (old_tabs if standalone else shell.tabs)
    assert host._diag_refresh is old_refresh if standalone else callable(host._diag_refresh)
    kobo.library_dir_row.text = '/edited'
    assert capture_directory[0]() == '/edited'
    for name in ('save', 'save_bottom'): getattr(bars, name).emit('clicked')
    for name in ('reconnect', 'reconnect_bottom'): getattr(bars, name).emit('clicked')
    assert save.call_count == reconnect.call_count == 2
    assert actions['save'] == (bars.save, bars.save_bottom)
    page.dispose(); page.dispose()
    assert not page.lifetime.alive
    if not standalone:
        host._diag_refresh()
        refresh.assert_not_called()


def test_editor_loading_keeps_bound_ids_and_choosers_read_current_directory(monkeypatch):
    configured = [{'id': 'identite', 'label': 'Initial'}]
    rows = [{'id': 'paris-default', 'provider': 'paris'}, {'id': 'unavailable', 'provider': 'offline'}]
    host = NS(config=NS(identities=lambda: configured, provider_accounts=lambda: rows),
              service=NS(storage=NS(adept_profile_binding_get=Mock(return_value='bound')),
                         normal_paris_adept_status=Mock(return_value={})),
              _choose_identity_directory=Mock(), _choose_adept_profile=Mock(), navigate=Mock())
    identity_controls, account_controls = [], []
    def identity_builder(**kwargs):
        editor = {'id': Widget(kwargs['identity_id']), 'directory': Widget(), 'adept_profile': Widget(kwargs['adept_profile']), 'group': Widget(), 'deleted': False}
        controls = [Widget(), Widget(), Widget()]
        identity_controls.append((kwargs, editor, controls))
        return editor, *controls
    def account_builder(**kwargs):
        editor = {'id': kwargs['account_id'], 'group': Widget(), 'deleted': False}
        delete = Widget(); account_controls.append(delete)
        return editor, delete
    monkeypatch.setattr(settings_editor_actions, 'build_identity_settings_header', lambda **kwargs: Widget())
    monkeypatch.setattr(settings_editor_actions, 'build_provider_accounts_header', lambda **kwargs: NS(plugins_button=Widget(), accounts_container=Widget()))
    monkeypatch.setattr(settings_editor_actions, 'build_identity_settings_editor', identity_builder)
    monkeypatch.setattr(settings_editor_actions, 'build_provider_account_editor', account_builder)
    add_account = Widget()
    monkeypatch.setattr(settings_editor_actions, 'build_provider_account_buttons', lambda **kwargs: [(add_account, 'paris')])
    box, dirty = Widget(), Mock()
    current_directory = ['/initial']
    identities, accounts = settings_editor_actions.build_settings_editors(
        host, box=box, descriptors={'paris': NS(state='ready')}, public_ids=set(),
        mark_settings_dirty=dirty, library_directory=lambda: current_directory[0],
        Gtk=NS(Button=Widget), Adw=NS(), add_css=Mock(),
    )
    assert [x['id'] for x in accounts] == ['paris-default']
    assert identities[0]['adept_profile'].get_text() == 'bound'
    host.service.storage.adept_profile_binding_get.assert_called_once_with('identity', 'identite')
    current_directory[0] = '/changed'
    identity_controls[0][2][0].emit('clicked')
    host._choose_identity_directory.assert_called_once_with(identities[0]['directory'], Path('/changed'))
    identity_controls[0][2][1].emit('clicked')
    host._choose_adept_profile.assert_called_once_with(identities[0]['adept_profile'], identity_controls[0][2][1])
    box.children[0].emit('clicked'); box.children[0].emit('clicked')
    assert [x['id'].get_text() for x in identities] == ['identite', 'identite-2', 'identite-3']
    add_account.emit('clicked')
    assert [x['id'] for x in accounts] == ['paris-default', 'paris-2']
    identity_controls[0][2][2].emit('clicked'); account_controls[0].emit('clicked')
    assert identities[0]['deleted'] and accounts[0]['deleted']
    assert not identities[0]['group'].visible and not accounts[0]['group'].visible
    assert dirty.call_count == 5
    assert configured == [{'id': 'identite', 'label': 'Initial'}]


def test_diagnostic_factory_binds_controls_and_keeps_late_refresh_inert(monkeypatch):
    ui = NS(**{name: Widget() for name in ('summary', 'filter', 'issues_box', 'details_toggle', 'text', 'scroll', 'refresh', 'copy', 'export', 'clear', 'performance_capture', 'performance_export', 'performance_status', 'open_page')})
    app = NS(level_dropdown=Widget(), diagnostics_switch=Widget(), levels=['normal'])
    host, lifetime = NS(_closed=False, _pages={}, navigate=Mock()), SettingsPageLifetime()
    callbacks = tuple(Mock() for _ in range(4))
    monkeypatch.setattr(settings_diagnostics_actions, 'build_settings_diagnostic_callbacks', lambda *args, **kwargs: callbacks)
    refresh = Mock()
    monkeypatch.setattr(settings_diagnostics_actions.settings_diagnostics_refresh_controller, 'refresh_settings_diagnostics', refresh)
    refresh_text = settings_diagnostics_actions.bind_settings_diagnostics(host, page_lifetime=lifetime, diagnostic_ui=ui, application_ui=app, Gtk=NS(), Adw=NS(), Gdk=NS(), clear_box=Mock())
    assert refresh.call_count == 1
    assert ui.open_page.sensitive is False  # Optional page is absent in this fixture.
    assert 'indisponible' in ui.open_page.tooltip
    ui.open_page.emit('clicked')
    host.navigate.assert_not_called()
    host._pages['diagnostics'] = object()
    ui.refresh.emit('clicked'); ui.filter.emit('notify::selected')
    assert ui.open_page.sensitive is True
    ui.open_page.emit('clicked')
    host.navigate.assert_called_once_with('diagnostics')
    # Diagnostics owns the tools now: hidden fallback data is not recollected.
    assert refresh.call_count == 1
    host._pages.clear()
    ui.refresh.emit('clicked')
    assert refresh.call_count == 2
    assert ui.open_page.sensitive is False
    host._pages['diagnostics'] = object()
    ui.refresh.emit('clicked')
    assert refresh.call_count == 2
    ui.copy.emit('clicked'); ui.export.emit('clicked'); ui.clear.emit('clicked')
    app.level_dropdown.emit('notify::selected'); app.diagnostics_switch.emit('notify::active')
    assert [fn.call_count for fn in callbacks] == [1, 1, 1, 1]
    ui.details_toggle.active = True; ui.details_toggle.emit('toggled')
    assert ui.scroll.visible and ui.details_toggle.label == 'Masquer les détails techniques'
    lifetime.dispose(); refresh_text(); ui.refresh.emit('clicked')
    ui.open_page.emit('clicked')
    assert refresh.call_count == 2
    host.navigate.assert_called_once_with('diagnostics')


def test_sync_factory_routes_to_scoped_page_without_global_acquisition():
    ui = NS(**{name: Widget() for name in ('backup_switch', 'backup_dir_row', 'backup_interval_row', 'vault_profile_drop', 'run_backup_now', 'backup_status')})
    ui.backup_switch.active = True
    host = NS(config={'kobo_backup.enabled': True},
              run_kobo_backup=Mock(), navigate=Mock())
    lifetime, state = SettingsPageLifetime(), SettingsRevisionState(['sync'])
    refresh = bind_settings_sync_actions(host, page_lifetime=lifetime, settings_state=state, kobo_settings=ui)
    host.run_kobo_backup.assert_not_called()
    ui.run_backup_now.emit('clicked')
    host.navigate.assert_called_once_with('sync')
    host.run_kobo_backup.assert_not_called()
    state.mark_dirty('sync'); ui.backup_switch.emit('notify::active')
    assert not ui.run_backup_now.sensitive
    ui.run_backup_now.emit('clicked')
    host.navigate.assert_called_once_with('sync')
    lifetime.dispose(); refresh(); ui.run_backup_now.emit('clicked')
    host.navigate.assert_called_once_with('sync')
    host.run_kobo_backup.assert_not_called()


def test_facade_is_compact_and_importing_composition_does_not_load_gtk():
    path = Path(__file__).resolve().parents[1] / 'src/cifereca/gui/main_window.py'
    window = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
    facade = next(n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == '_build_settings_page')
    assert facade.end_lineno - facade.lineno < 10
    assert len(facade.body) == 1
    assert 'settings_page.build_settings_page' in ast.unparse(facade)
    import subprocess, sys
    subprocess.run([sys.executable, '-c', "import sys; from cifereca.gui.settings_page import build_settings_page; assert 'gi' not in sys.modules; assert 'cifereca.gui.main_window' not in sys.modules"], check=True)



def test_settings_save_does_not_capture_retired_global_kobo_consent():
    """#3731: legacy switch remains visible read-only, never a save input."""
    root = Path(__file__).resolve().parents[1]
    commit = ast.parse((root / "src/cifereca/gui/settings_commit_actions.py").read_text(encoding="utf-8"))
    source = ast.unparse(commit)
    assert "values['backup_enabled']" not in source
    assert "backup_switch.get_active()" not in source
    # Compatibility settings remain editable without generating acquisition permission.
    assert "values['backup_directory']" in source
    assert "values['backup_interval']" in source
    assert "values['vault_profile_index']" in source
