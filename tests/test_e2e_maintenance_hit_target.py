"""P1 #2804: 920px tool buttons must be GTK-hit-tested before XTEST."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "e2e/maintenance_hit_target.py"
spec = importlib.util.spec_from_file_location("maintenance_hit_target_test", MODULE)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Node:
    def __init__(self, parent=None):
        self.parent = parent

    def get_parent(self):
        return self.parent


class Bounds:
    def get_x(self):
        return 640

    def get_y(self):
        return 160  # wrapped action row at the 920px viewport

    def get_width(self):
        return 100

    def get_height(self):
        return 32


class Window:
    def __init__(self, button, *, mapped_y=176, width=920):
        self.button = button
        self.mapped_y = mapped_y
        self.width = width
        self.picks = []

    def get_width(self):
        return self.width

    def get_height(self):
        return 620

    def pick(self, x, y, flags):
        self.picks.append((x, y, flags))
        if self.mapped_y is None or abs(y - self.mapped_y) > 1:
            return Node()
        return Node(self.button)


def test_wrapped_920px_action_chooses_a_real_button_descendant():
    button = Node()
    window = Window(button)
    position = module.choose_verified_point(window, button, Bounds(), 0)
    assert position == (690, 176)
    assert window.picks
    assert (690, 144, 0) not in window.picks[:1] or position != (690, 144)


def test_fixed_y_144_is_not_clicked_without_gtk_hit_verification():
    button = Node()
    window = Window(button, mapped_y=None)
    with pytest.raises(RuntimeError, match="no verified hit target"):
        module.choose_verified_point(window, button, Bounds(), 0)
    assert (690, 144, 0) in window.picks  # evaluated, not clicked
    assert len(window.picks) < 130  # bounded local geometry scan


def test_original_desktop_row_at_y144_remains_usable():
    button = Node()
    window = Window(button, mapped_y=144, width=1366)
    assert module.choose_verified_point(window, button, Bounds(), 0) == (690, 144)


def test_no_point_outside_toplevel_is_dispatched():
    button = Node()
    window = Window(button, mapped_y=176, width=500)
    with pytest.raises(RuntimeError, match="no horizontal on-screen"):
        module.choose_verified_point(window, button, Bounds(), 0)
    assert window.picks == []
