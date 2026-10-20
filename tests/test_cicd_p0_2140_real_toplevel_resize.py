"""#2140 CICD: mapped GTK windows must receive real bounded X11 resize requests."""
from __future__ import annotations

import ctypes as C
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

from tests.test_e2e_xtest_pointer_guard import _x11_class


class FakeXlibFunction:
    def __init__(self, action):
        self.action = action
    def __call__(self, *args):
        return self.action(*args)


class FakeToplevel:
    def __init__(self):
        self.surface = object()
    def get_native(self):
        return self
    def get_surface(self):
        return self.surface


@pytest.mark.parametrize("width,height", [
    (0, 790), (-1, 790), (True, 790), (760, False),
    (8193, 790), (760, 8193), ("760", 790),
])
def test_invalid_resize_dimensions_do_not_use_native_x11(monkeypatch, width, height):
    device = object.__new__(_x11_class())
    device.display = 123
    device.x11 = NS(XResizeWindow=FakeXlibFunction(
        lambda *_args: pytest.fail("X11 request must not be sent")
    ))
    with pytest.raises(RuntimeError, match="dimensions invalid"):
        device.resize_toplevel(FakeToplevel(), width, height)


def test_resize_targets_native_xid_and_syncs_x11_without_fake_gtk_allocation(monkeypatch):
    device = object.__new__(_x11_class())
    device.display = 123
    calls = []
    device.x11 = NS(
        XResizeWindow=FakeXlibFunction(
            lambda *args: calls.append(("resize", args)) or 1
        ),
        XSync=FakeXlibFunction(lambda *args: calls.append(("sync", args))),
    )
    monkeypatch.setitem(sys.modules, "native_capture", NS(pointer=lambda surface: 42))
    monkeypatch.setattr(C, "CDLL", lambda *_: NS(
        gdk_x11_surface_get_xid=FakeXlibFunction(lambda _ptr: 333),
    ))
    window = FakeToplevel()
    device.resize_toplevel(window, 760, 790)
    assert calls == [
        ("resize", (123, 333, 760, 790)),
        ("sync", (123, False)),
    ]


def test_diagnostics_probe_uses_real_x11_resize_and_keeps_strict_760px_gate():
    source = (Path(__file__).resolve().parents[1]
              / "e2e/diagnostics_page_probe.py").read_text()
    assert "device[0].resize_toplevel(window[0], 760, 790)" in source
    assert "window[0].get_width() <= 760" in source
    assert "700 <= window[0].get_width() <= 760" in source
    assert 'horizontal.get_upper() - horizontal.get_page_size() < 5' in source
    assert 'P0_2140_selected_window_760_without_horizontal_overflow' in source
