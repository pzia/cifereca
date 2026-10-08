from __future__ import annotations


MASTER_DETAIL_NARROW_WINDOW_WIDTH = 960


def master_detail_is_narrow(
    window_width: int,
    *,
    breakpoint: int = MASTER_DETAIL_NARROW_WINDOW_WIDTH,
) -> bool:
    """Use actual top-level window width for master/detail responsiveness.

    A non-positive width means GTK has not completed the first allocation yet;
    keep the two-pane layout until a real window measurement is available.
    """
    try:
        width = int(window_width)
        threshold = int(breakpoint)
    except (TypeError, ValueError):
        return False
    return 0 < width < threshold
