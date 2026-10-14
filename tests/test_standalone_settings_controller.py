"""Real standalone Settings window assembly and lifetime/close callbacks."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

from cifereca.gui.standalone_settings_controller import open_standalone_settings


class Widget:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.children, self.controllers, self.signals = [], [], {}
        self.presented, self.close_calls = False, 0

    @classmethod
    def new_from_icon_name(cls, icon): return cls(icon_name=icon)
    def set_title(self, text): self.title = text
    def set_default_size(self, width, height): self.size = (width, height)
    def set_title_widget(self, widget): self.title_widget = widget
    def set_tooltip_text(self, text): self.tooltip = text
    def set_vexpand(self, value): self.expanded = value
    def set_content(self, content): self.content = content
    def pack_end(self, widget): self.children.append(widget)
    def append(self, widget): self.children.append(widget)
    def add_controller(self, controller): self.controllers.append(controller)
    def connect(self, signal, callback):
        self.signals.setdefault(signal, []).append(callback)
    def emit(self, signal, *args):
        return [callback(self, *args) for callback in self.signals.get(signal, [])]
    def close(self):
        self.close_calls += 1
        return self.emit('close-request')
    def present(self):
        # A synchronous presentation callback must see the owner reference.
        assert self.options['transient_for']._standalone_settings_window is self
        self.presented = True


class Lifetime:
    def __init__(self): self.dispose_calls = 0
    def dispose(self): self.dispose_calls += 1


def context():
    created, pages = [], []
    main_lifetime = Lifetime()
    host = NS(_closed=False, _settings_standalone_lifetime=None,
              _settings_main_lifetime=main_lifetime)
    def window(**kwargs):
        widget = Widget(**kwargs); created.append(widget); return widget
    def build_page(*, standalone=False):
        assert standalone is True
        if host._settings_standalone_lifetime is not None:
            host._settings_standalone_lifetime.dispose()
        host._settings_standalone_lifetime = Lifetime()
        page = Widget(); pages.append(page); return page
    host._build_settings_page = build_page
    ports = dict(Gtk=NS(Box=Widget, Label=Widget, Button=Widget, EventControllerKey=Widget,
                        Orientation=NS(VERTICAL='vertical')),
                 Adw=NS(Window=window, HeaderBar=Widget), Gdk=NS(KEY_Escape=27))
    return host, ports, created, pages


def test_renderer_retains_nonmodal_parent_size_header_and_standalone_page():
    host, ports, windows, pages = context()
    assert open_standalone_settings(host, **ports) is None
    win = windows[0]
    assert win.options == {'transient_for': host, 'modal': False}
    assert win.title == 'Diagnostic / Configuration'
    assert win.size == (900, 650)
    assert win.content.options == {'orientation': 'vertical', 'spacing': 0}
    header, page = win.content.children
    assert header.title_widget.options == {'label': 'Configuration et diagnostic'}
    assert page is pages[0] and page.expanded is True
    assert header.children[0].options == {'icon_name': 'window-close-symbolic'}
    assert header.children[0].tooltip == 'Fermer'
    assert win.presented
    assert host._settings_main_lifetime.dispose_calls == 0


def test_actual_close_button_releases_current_lifetime_once():
    host, ports, windows, _ = context()
    open_standalone_settings(host, **ports)
    lifetime = host._settings_standalone_lifetime
    button = windows[0].content.children[0].children[0]
    button.emit('clicked')
    assert lifetime.dispose_calls == 1
    assert host._settings_standalone_lifetime is None
    assert host._standalone_settings_window is None
    assert windows[0].close() == [False]
    assert lifetime.dispose_calls == 1
    assert host._settings_main_lifetime.dispose_calls == 0


def test_escape_closes_window_but_unrelated_key_is_not_consumed():
    host, ports, windows, _ = context()
    open_standalone_settings(host, **ports)
    key = windows[0].controllers[0]
    assert key.emit('key-pressed', 13, 0, 0) == [False]
    assert windows[0].close_calls == 0
    assert key.emit('key-pressed', 27, 0, 0) == [True]
    assert windows[0].close_calls == 1
    assert host._standalone_settings_window is None


def test_old_window_close_cannot_revoke_replacement_page_or_owner_reference():
    host, ports, windows, _ = context()
    open_standalone_settings(host, **ports)
    old_lifetime = host._settings_standalone_lifetime
    open_standalone_settings(host, **ports)
    current_lifetime = host._settings_standalone_lifetime
    assert old_lifetime.dispose_calls == 1
    windows[0].close()
    assert host._standalone_settings_window is windows[1]
    assert host._settings_standalone_lifetime is current_lifetime
    assert current_lifetime.dispose_calls == 0
    windows[1].close()
    assert current_lifetime.dispose_calls == 1
    assert host._settings_main_lifetime.dispose_calls == 0


def test_close_after_parent_teardown_tolerates_already_cleared_lifetime():
    host, ports, windows, _ = context()
    open_standalone_settings(host, **ports)
    host._settings_standalone_lifetime = None
    host._closed = True
    assert windows[0].close() == [False]
    assert host._standalone_settings_window is None


def test_late_open_after_parent_close_does_not_build_any_widget_or_page():
    host, ports, windows, pages = context()
    host._closed = True
    assert open_standalone_settings(host, **ports) is None
    assert windows == pages == []
    assert host._settings_standalone_lifetime is None


def test_stable_facade_and_controller_import_without_gtk():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'src/cifereca/gui/main_window.py').read_text(encoding='utf-8'))
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
    facade = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == '_open_prelogin_diagnostics')
    assert facade.end_lineno - facade.lineno < 6
    assert len(facade.args.args) == 1 and facade.args.args[0].arg == 'self'
    assert 'standalone_settings_controller.open_standalone_settings' in ast.unparse(facade)
    env = {**os.environ, 'PYTHONPATH': str(root / 'src')}
    subprocess.run([sys.executable, '-c', "import sys; import cifereca.gui.standalone_settings_controller; assert 'gi' not in sys.modules; assert 'cifereca.gui.main_window' not in sys.modules"], env=env, check=True)
