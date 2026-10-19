"""XTEST never sends pointer input for a GTK transient with invalid geometry."""
from __future__ import annotations

import importlib.util
import ctypes as C
from pathlib import Path
import sys
from types import SimpleNamespace as NS

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


@pytest.mark.parametrize("transform,origin,expected", [
    ((0, 0), (20, 30), (55, 78)),
    ((5, 5), (0, 0), (40, 53)),
    ((-3, -7), (100, 200), (132, 241)),
])
def test_native_widget_center_includes_surface_render_offset(
    monkeypatch, transform, origin, expected,
):
    class Function:
        def __init__(self, operation): self.operation = operation
        def __call__(self, *args): return self.operation(*args)
    def translate(_display, _xid, _root, _x, _y, out_x, out_y, _child):
        out_x._obj.value, out_y._obj.value = origin
        return 1
    device = object.__new__(_x11_class())
    device.display = 1
    device.x11 = NS(
        XDefaultRootWindow=Function(lambda *_: 99),
        XTranslateCoordinates=Function(translate),
    )
    monkeypatch.setitem(sys.modules, "native_capture", NS(pointer=lambda obj: 42))
    monkeypatch.setattr(C, "CDLL", lambda *_: NS(
        gdk_x11_surface_get_xid=Function(lambda *_: 7),
    ))
    native = NS(get_surface=lambda: object(), get_surface_transform=lambda: transform)
    rect = NS(get_x=lambda: 10, get_y=lambda: 40,
              get_width=lambda: 50, get_height=lambda: 16)
    widget = NS(get_native=lambda: native, compute_bounds=lambda target: (True, rect))
    assert device.widget_point(widget) == expected
