from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path

from cifereca.gui.action_layout import visible_action_count


ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ROOT / "src/cifereca/gui/action_widgets.py"
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(gui_source_text(path))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_overflow_policy_keeps_leading_primary_action_visible():
    assert visible_action_count([120, 110, 100], 170) == 1
    assert visible_action_count([120, 110, 100], 285) == 2
    assert visible_action_count([120, 110, 100], 400) == 3


def test_detail_panel_enables_named_primary_and_overflow_mode():
    source = _method(MAIN, "DetailPanel", "__init__")
    assert "overflow=True" in source
    assert "preserve_primary_label=True" in source


def test_overflow_reparents_same_button_objects_in_priority_order():
    source = _method(ACTIONS, "AdaptiveActionBar", "_layout_overflow")
    assert "self._buttons[:visible_count]" in source
    assert "self._buttons[visible_count:]" in source
    assert "self._move_button(button, self.box)" in source
    assert "self._move_button(button, self.overflow_box)" in source
    assert "self.box.append(self.overflow_button)" in source


def test_overflow_is_named_and_accessible():
    source = _method(ACTIONS, "AdaptiveActionBar", "__init__")
    assert "view-more-symbolic" in source
    assert "Plus d’actions" in source
    assert "Gtk.AccessibleProperty.LABEL" in source


def test_primary_label_is_restored_in_overflow_mode():
    source = _method(ACTIONS, "AdaptiveActionBar", "_resize")
    assert "self.preserve_primary_label and self._buttons" in source
    assert "primary = self._buttons[0]" in source
    assert "content.set_label(label)" in source
