from __future__ import annotations

import pytest

from cifereca.gui.layout import (
    MASTER_DETAIL_NARROW_WINDOW_WIDTH,
    master_detail_is_narrow,
)


def test_master_detail_breakpoint_is_based_on_window_width():
    assert MASTER_DETAIL_NARROW_WINDOW_WIDTH == 960


@pytest.mark.parametrize("width", [1280, 1300, 1366])
def test_desktop_window_widths_keep_master_detail(width):
    assert master_detail_is_narrow(width) is False


@pytest.mark.parametrize("width", [640, 800, 959])
def test_true_narrow_windows_use_exclusive_list_detail(width):
    assert master_detail_is_narrow(width) is True


def test_unallocated_window_does_not_hide_detail_prematurely():
    assert master_detail_is_narrow(0) is False
