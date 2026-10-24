"""The narrow GTK gate must test an allocated child of a correctly sized host."""
from pathlib import Path
import importlib.util
from types import SimpleNamespace as NS

import pytest


path = Path(__file__).resolve().parents[1] / 'e2e/library_inspection_acceptance.py'
spec = importlib.util.spec_from_file_location('inspection_fixture_geometry', path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize('width,height,expected', [
    (870, 570, (870, 570)),  # actual 920x620 runner, after its chrome allowance
    (974, 718, (920, 620)),
    (1230, 750, (920, 620)),
    (1550, 950, (920, 620)),
])
def test_synthetic_host_never_exceeds_its_allocated_parent(width, height, expected):
    parent = NS(get_width=lambda: width, get_height=lambda: height)
    assert probe._inspection_host_size(parent) == expected


@pytest.mark.parametrize('session,dialog,mapped,width,height,ready', [
    (None, True, True, 876, 576, False),
    (object(), False, True, 876, 576, False),
    (object(), True, False, 876, 576, False),
    (object(), True, True, 0, 0, False),
    (object(), True, True, 876, 0, False),
    (object(), True, True, 0, 576, False),
    (object(), True, True, 876, 576, True),
])
def test_mapped_but_unallocated_window_cannot_pass_fit_checks(
    session, dialog, mapped, width, height, ready,
):
    window = NS(get_mapped=lambda: mapped, get_width=lambda: width,
                get_height=lambda: height) if dialog else None
    controller = NS(session=session, dialog=window)
    assert bool(probe._inspection_geometry_ready(controller)) is ready
