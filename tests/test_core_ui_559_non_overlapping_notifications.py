from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(class_name: str, method_name: str) -> str:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_notifications_reserve_layout_space_instead_of_overlaying_content():
    source = _method("MainWindow", "__init__")
    assert "Adw.ToastOverlay" not in source
    assert "self.window_root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)" in source
    assert "self.window_root.append(self.root_stack)" in source
    assert "self.window_root.append(self.notification_revealer)" in source
    assert source.index("self.window_root.append(self.root_stack)") < source.index(
        "self.window_root.append(self.notification_revealer)"
    )


def test_notification_bar_is_closable_and_outside_detail_viewports():
    source = _method("MainWindow", "__init__")
    assert "self.notification_close = Gtk.Button.new_from_icon_name('window-close-symbolic')" in source
    assert "Fermer la notification" in source
    assert "self._dismiss_notification()" in source
    assert "self.notification_label.set_wrap(True)" in source


def test_notification_timeout_is_bounded_and_reuses_single_bar():
    source = _method("MainWindow", "toast")
    assert "self._cancel_notification_hide()" in source
    assert "self.notification_label.set_label(str(message))" in source
    assert "self.notification_revealer.set_reveal_child(True)" in source
    assert "bounded_timeout = bounded_seconds(timeout)" in source
    assert "GLib.timeout_add_seconds" in source
    assert "Adw.Toast" not in source


def test_explicit_dismissal_cancels_timeout_before_hiding():
    source = _method("MainWindow", "_dismiss_notification")
    assert "self._cancel_notification_hide()" in source
    assert "self.notification_revealer.set_reveal_child(False)" in source
