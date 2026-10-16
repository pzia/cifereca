"""XTEST never sends pointer input for a GTK transient with invalid geometry."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _x11_class():
    path = Path(__file__).resolve().parents[1] / "environnements/openai-chat-debian/x11_input.py"
    spec = importlib.util.spec_from_file_location("cifereca_xtest_input", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.X11Input


class _Widget:
    def get_name(self):
        return "GtkButton"


@pytest.mark.parametrize("coordinates", [(-5, -5), (-1, 25), (35, -1)])
def test_modal_pointer_with_invalid_coordinates_emits_no_x11_event(coordinates):
    device = object.__new__(_x11_class())
    clicks = []
    device.widget_point = lambda widget: coordinates
    device.click = lambda x, y: clicks.append((x, y))
    with pytest.raises(RuntimeError, match="no on-screen pointer coordinates"):
        device.click_widget(_Widget())
    assert clicks == []


def test_valid_coordinates_emit_exactly_one_real_xtest_click():
    device = object.__new__(_x11_class())
    clicks = []
    device.widget_point = lambda widget: (115, 225)
    device.click = lambda x, y: clicks.append((x, y))
    result = device.click_widget(_Widget())
    assert clicks == [(115, 225)]
    assert result == {
        "widget": "GtkButton",
        "x": 115,
        "y": 225,
        "source": "XTEST pointer/button events",
    }
