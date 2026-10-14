"""Real notification renderer and constructor signal wiring with GTK doubles."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

from cifereca.gui.notification_widgets import build_notification_bar
from cifereca.gui import feedback_timers


class Widget:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.children = []
        self.signals = {}

    @classmethod
    def new_from_icon_name(cls, icon):
        return cls(icon_name=icon)

    def set_transition_type(self, value): self.transition = value
    def set_reveal_child(self, value): self.revealed = value
    def set_child(self, child): self.children = [child]
    def set_hexpand(self, value): self.expanded = value
    def set_wrap(self, value): self.wrapped = value
    def set_tooltip_text(self, value): self.tooltip = value
    def set_label(self, value): self.options['label'] = value
    def append(self, child): self.children.append(child)
    def connect(self, signal, callback):
        self.signals.setdefault(signal, []).append(callback)
    def click(self):
        for callback in self.signals.get('clicked', []): callback(self)


Gtk = NS(
    Revealer=Widget, Box=Widget, Label=Widget, Button=Widget,
    Orientation=NS(HORIZONTAL='horizontal'),
    RevealerTransitionType=NS(SLIDE_UP='slide-up'),
)
ROOT = Path(__file__).resolve().parents[1]


def test_renderer_preserves_hidden_bar_order_spacing_labels_and_icon():
    css = []
    bar = build_notification_bar(Gtk=Gtk, add_css=lambda *args: css.append(args))
    row = bar.revealer.children[0]
    assert bar.revealer.transition == 'slide-up'
    assert bar.revealer.revealed is False
    assert row.options == {'orientation': 'horizontal', 'spacing': 8}
    assert row.children == [bar.label, bar.close]
    assert css == [(row, 'notification-bar')]
    assert bar.label.options == {'label': '', 'xalign': 0}
    assert bar.label.expanded and bar.label.wrapped
    assert bar.close.options == {'icon_name': 'window-close-symbolic'}
    assert bar.close.tooltip == 'Fermer la notification'
    assert not bar.revealer.signals and not bar.close.signals


def _construct_host():
    removed = []
    stack = Widget()
    host = NS(_closed=False, window_root=Widget())
    host.window_root.append(stack)
    host._cancel_notification_hide = lambda: feedback_timers.cancel_notification_hide(
        host, source_remove=removed.append,
    )
    host._dismiss_notification = lambda: feedback_timers.dismiss_notification(host)
    host._hide_notification = lambda: feedback_timers.hide_notification(host)
    tree = ast.parse((ROOT / 'src/cifereca/gui/main_window.py').read_text(encoding='utf-8'))
    window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
    init = next(n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    first = next(i for i, n in enumerate(init.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'notification' for t in n.targets))
    last = next(i for i, n in enumerate(init.body) if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Attribute) and n.target.attr == '_notification_hide_source')
    scope = {'self': host, 'Gtk': Gtk, 'add_css': lambda *args: None,
             'build_notification_bar': build_notification_bar}
    exec(compile(ast.fix_missing_locations(ast.Module(body=init.body[first:last + 1], type_ignores=[])), '<real MainWindow notification wiring>', 'exec'), scope)
    return host, stack, scope['notification'], removed


def test_real_constructor_binds_exact_widgets_once_below_stack():
    host, stack, bar, _ = _construct_host()
    assert host.notification_revealer is bar.revealer
    assert host.notification_label is bar.label
    assert host.notification_close is bar.close
    assert host.window_root.children == [stack, bar.revealer]
    assert len(bar.close.signals['clicked']) == 1
    assert host._notification_hide_source is None


def test_toast_reuses_rendered_bar_and_actual_close_cancels_timer():
    host, _, bar, removed = _construct_host()
    scheduled = []
    def schedule(seconds, callback):
        scheduled.append((seconds, callback))
        return len(scheduled)
    feedback_timers.toast(host, 'first', 3, timeout_add_seconds=schedule)
    feedback_timers.toast(host, 'second', 5, timeout_add_seconds=schedule)
    assert host.notification_revealer is bar.revealer
    assert host.notification_label is bar.label
    assert bar.label.options['label'] == 'second'
    assert bar.revealer.revealed
    assert removed == [1]
    assert host._notification_hide_source == 2
    assert [seconds for seconds, _ in scheduled] == [3, 5]
    bar.close.click()
    assert removed == [1, 2]
    assert host._notification_hide_source is None
    assert not bar.revealer.revealed


def test_detached_close_button_after_window_close_cannot_touch_widgets_or_timer():
    host, _, bar, removed = _construct_host()
    bar.revealer.set_reveal_child(True)
    host._notification_hide_source = 9
    host._closed = True
    bar.close.click()
    assert removed == []
    assert host._notification_hide_source == 9
    assert bar.revealer.revealed


def test_renderer_import_does_not_load_gtk_or_main_window():
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
    subprocess.run([sys.executable, '-c',
                    "import sys; import cifereca.gui.notification_widgets; assert 'gi' not in sys.modules; assert 'cifereca.gui.main_window' not in sys.modules"], env=env, check=True)
