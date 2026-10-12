"""P0 #1188: the report wheel moves only its bounded inner adjustment."""
from __future__ import annotations

from types import SimpleNamespace

from cifereca_diagnostics_page import _scroll_report_adjustment


Gdk = SimpleNamespace(ScrollUnit=SimpleNamespace(WHEEL=1, SURFACE=2))


class Adjustment:
    def __init__(self, *, value=0.0, lower=0.0, upper=1300.0, page=560.0):
        self.value, self.lower, self.upper, self.page = value, lower, upper, page
        self.writes = []

    def get_value(self):
        return self.value

    def get_lower(self):
        return self.lower

    def get_upper(self):
        return self.upper

    def get_page_size(self):
        return self.page

    def set_value(self, value):
        self.writes.append(value)
        self.value = value


def test_xtest_wheel_steps_one_inner_page_only_and_clamps():
    inner = Adjustment()
    outer = Adjustment(value=290.0, upper=4000.0, page=870.0)
    for _ in range(4):
        assert _scroll_report_adjustment(inner, 1.0, Gdk.ScrollUnit.WHEEL, Gdk)
    assert 250 < inner.get_value() < 300
    assert outer.get_value() == 290.0
    assert not outer.writes
    assert _scroll_report_adjustment(inner, 30.0, Gdk.ScrollUnit.WHEEL, Gdk)
    assert inner.get_value() == 740.0
    assert not _scroll_report_adjustment(inner, 1.0, Gdk.ScrollUnit.WHEEL, Gdk)


def test_upward_and_surface_scrolling_and_edges():
    inner = Adjustment(value=100.0)
    assert _scroll_report_adjustment(inner, 12.0, Gdk.ScrollUnit.SURFACE, Gdk)
    assert inner.get_value() == 130.0
    assert _scroll_report_adjustment(inner, -60.0, Gdk.ScrollUnit.SURFACE, Gdk)
    assert inner.get_value() == 0.0
    assert not _scroll_report_adjustment(inner, -1.0, Gdk.ScrollUnit.WHEEL, Gdk)


def test_invalid_or_zero_delta_does_not_change_adjustment():
    inner = Adjustment(value=12.0)
    for delta in (0.0, float("nan"), float("inf"), -float("inf")):
        assert not _scroll_report_adjustment(inner, delta, Gdk.ScrollUnit.WHEEL, Gdk)
    assert inner.get_value() == 12.0
    assert inner.writes == []


def test_root_capture_is_installed_before_gtk_outer_scroll_window():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py").read_text(
        encoding="utf-8",
    )
    assert "page_root.append(scroll)" in source
    assert "wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)" in source
    assert "_install_report_wheel(\n            Gtk, Gdk, page_root" in source
    assert "tracking.connect(\"motion\", pointer_moved)" in source
    assert "tracking.connect(\"enter\", pointer_moved)" in source
    assert "tracking.connect(\"leave\", pointer_left)" in source
    assert "root.pick(*position, Gtk.PickFlags.DEFAULT)" in source
    assert "picked.get_parent()" in source
    assert "return page_root" in source
