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


class _TopLevel:
    def __init__(self, width=1500, height=850):
        self.width, self.height = width, height

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height


def test_audited_local_window_point_translates_from_native_x11_center():
    device = object.__new__(_x11_class())
    window = _TopLevel()
    seen = []
    device.widget_point = lambda native: (860, 525)
    device.click = lambda x, y: seen.append((x, y))
    x, y = device.window_point(window, 400, 300)
    assert (x, y) == (510, 400)
    device.click(x, y)
    assert seen == [(510, 400)]  # one genuine XTEST dispatch, no Gtk.clicked()


@pytest.mark.parametrize("local_point", (
    (-1, 10), (10, -1), (1500, 10), (10, 850),
    (float("nan"), 10), (10, float("inf")), (True, 10), ("40", 10),
))
def test_invalid_toplevel_coordinate_never_attempts_xtest(local_point):
    device = object.__new__(_x11_class())
    device.widget_point = lambda native: (_ for _ in ()).throw(
        AssertionError("Must reject before touching native X11")
    )
    with pytest.raises(RuntimeError, match="outside allocated"):
        device.window_point(_TopLevel(), *local_point)


def test_kobo_audit_still_uses_real_x11_and_fails_on_missing_clicked_signal():
    root = Path(__file__).resolve().parents[1]
    source = (root / "e2e/diagnostics_navigation_probe.py").read_text()
    assert 'picked = w.pick(point_x, point_y, Gtk.PickFlags.DEFAULT)' in source
    assert 'screen_x, screen_y = device[0].window_point(w, point_x, point_y)' in source
    assert 'device[0].move(screen_x, screen_y)' in source
    assert 'device[0].click(sx, sy)' in source
    assert 'loan_sync_audit_after_motion' in source
    assert 'assert attempts <= 5' in source
    assert 'f["audit_click_signal_seen"] = False' in source
    assert 'f["audit_deadline"] = monotonic() + 6' in source
    assert 'device[0].click_widget(audit)' not in source


def test_xtest_move_sends_only_motion_and_synchronizes_before_button_press():
    device = object.__new__(_x11_class())
    calls = []
    class _Motion:
        def __call__(self, *args):
            calls.append(("motion", args))
    class _Button:
        def __call__(self, *args):
            calls.append(("button", args))
    class _Sync:
        def __call__(self, *args):
            calls.append(("sync", args))
    device.display = 123
    device.xtst = NS(XTestFakeMotionEvent=_Motion(), XTestFakeButtonEvent=_Button())
    device.x11 = NS(XSync=_Sync())
    device.move(300, 400)
    assert [name for name, _args in calls] == ["motion", "sync"]
    assert calls[0][1][2:4] == (300, 400)
    calls.clear()
    device.click(300, 400)
    assert [name for name, _args in calls] == [
        "motion", "sync", "button", "button", "sync",
    ]
    assert [args[2] for name, args in calls if name == "button"] == [True, False]


@pytest.mark.parametrize(("down", "button"), [(True, 5), (False, 4)])
def test_wheel_dispatches_only_bounded_real_xtest_notches(down, button):
    device = object.__new__(_x11_class())
    calls = []
    device.display = 123
    device.move = lambda x, y: calls.append(("move", x, y))
    device.xtst = NS(XTestFakeButtonEvent=lambda *args: calls.append(("button", args)))
    device.x11 = NS(XSync=lambda *args: calls.append(("sync", args)))
    device.wheel(300, 400, down=down, ticks=2)
    assert calls[0] == ("move", 300, 400)
    assert [args[1][1:3] for args in calls if args[0] == "button"] == [
        (button, True), (button, False),
        (button, True), (button, False),
    ]
    assert calls[-1][0] == "sync"


@pytest.mark.parametrize("args", [
    {"x": -1, "y": 100},
    {"x": 100, "y": -1},
    {"x": True, "y": 100},
    {"x": 100, "y": 100, "ticks": 0},
    {"x": 100, "y": 100, "ticks": 21},
    {"x": 100, "y": 100, "ticks": True},
    {"x": 100, "y": 100, "down": "yes"},
])
def test_invalid_wheel_target_never_dispatches_xtest(args):
    device = object.__new__(_x11_class())
    device.move = lambda *_: pytest.fail("invalid wheel should not move mouse")
    with pytest.raises(ValueError, match="invalid_xtest_wheel_target"):
        device.wheel(**args)
