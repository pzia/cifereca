from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ROOT / "src/cifereca/gui/action_widgets.py"


def _method_source(class_name: str, method_name: str) -> str:
    tree = ast.parse(ACTIONS.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def _function_source(name: str) -> str:
    tree = ast.parse(ACTIONS.read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(fn)


def test_adaptive_bar_compacts_without_replacing_button_objects():
    init = _method_source("AdaptiveActionBar", "__init__")
    append = _method_source("AdaptiveActionBar", "append")
    resize = _method_source("AdaptiveActionBar", "_resize")

    assert "self._buttons = []" in init
    assert "self._buttons.append(button)" in append
    assert "for button in self._buttons:" in resize
    assert "self._buttons =" not in resize


def test_compact_mode_preserves_accessible_label_and_button_order():
    resize = _method_source("AdaptiveActionBar", "_resize")

    assert "button.set_child(Gtk.Image.new_from_icon_name(icon))" in resize
    assert "button.update_property([Gtk.AccessibleProperty.LABEL], [label])" in resize

    # Iterating the original list preserves insertion order through compact/restore.
    assert "for button in self._buttons:" in resize


def test_restore_mode_reuses_original_labels_and_icons():
    resize = _method_source("AdaptiveActionBar", "_resize")

    assert "content.set_label(label)" in resize
    assert "content.set_icon_name(icon)" in resize
    assert "button.set_child(content)" in resize


def test_mirror_sensitivity_is_bidirectional():
    source = _function_source("mirror_sensitivity")

    assert "first.connect('notify::sensitive', changed, second)" in source
    assert "second.connect('notify::sensitive', changed, first)" in source
    assert "if target.get_sensitive() != value:" in source
    assert "target.set_sensitive(value)" in source
