"""P1 #774: XTEST must address physical X11 pixels, not logical GTK units.

Pure offset tests: no X server, provider, profile, network or GTK dependency.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cifereca_e2e_x11_input", ROOT / "environnements/openai-chat-debian/x11_input.py"
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize(
    ("logical", "scale", "physical"),
    [(0, 1, 0), (22, 1, 22), (22, 2, 44),
     (-13.5, 2, -27), (4.25, 2, 8), (25.5, 1, 26)],
)
def test_gtk_offsets_map_to_actual_physical_screen_pixels(logical, scale, physical):
    assert module._gtk_to_x11_offset(logical, scale) == physical


@pytest.mark.parametrize("invalid", [0, -1, 5, None, True, 2.0, "2"])
def test_x11_scale_must_be_bounded_integer(invalid):
    with pytest.raises(ValueError, match="invalid_gtk_x11_scale"):
        module._gtk_to_x11_offset(20, invalid)


def test_window_hit_point_uses_scale_without_changing_gtk_hit_test():
    device = object.__new__(module.X11Input)
    device.widget_point = lambda window: (600, 400)
    view = NS(get_width=lambda: 400, get_height=lambda: 300,
              get_scale_factor=lambda: 2)
    # At scale 2, a logical 10x20 displacement is 20x40 X11 pixels.
    assert device.window_point(view, 210, 170) == (620, 440)
    view.get_scale_factor = lambda: 1
    assert device.window_point(view, 210, 170) == (610, 420)


@pytest.mark.parametrize("point", [(-1, 10), (400, 10), (2, 300)])
def test_window_hit_point_never_emits_out_of_bounds_coordinates(point):
    device = object.__new__(module.X11Input)
    device.widget_point = lambda window: (600, 400)
    window = NS(get_width=lambda: 400, get_height=lambda: 300,
                get_scale_factor=lambda: 2)
    with pytest.raises(RuntimeError, match="outside allocated GTK toplevel"):
        device.window_point(window, *point)
