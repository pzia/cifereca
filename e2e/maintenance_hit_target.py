"""GTK probe-only, side-effect-free selection of a verified pointer hit target.

No screenshots, X11 events, widget labels, paths, credentials or text. The
caller dispatches exactly one XTEST click *after* a GTK pick proves the target.
"""
from __future__ import annotations


def choose_verified_point(window, button, bounds, pick_flag) -> tuple[int, int]:
    """Find a GTK-window-local button point, including 920px wrapped rows.

    Gtk.Widget.compute_bounds may report an unsuitable y under rootless Xvfb.
    Gtk.Widget.pick validates the actual toplevel hit tree instead of
    assuming the row stays at y=144 at all responsive widths.
    """
    width = window.get_width()
    height = window.get_height()
    x = round(bounds.get_x() + bounds.get_width() / 2)
    if not 0 <= x < width or not height > 0:
        raise RuntimeError("GTK action has no horizontal on-screen hit target")

    candidates = [
        round(bounds.get_y() + bounds.get_height() / 2),
        144,  # old, already attested desktop point: a hint, not authority
    ]
    # Only a bounded local search around the visible header/first tool row.
    # The GTK pick operation does not deliver clicks or mutate widget state.
    candidates.extend(range(75, min(height - 1, 360), 3))
    seen = set()
    for y in candidates:
        if y in seen or not 0 <= y < height:
            continue
        seen.add(y)
        try:
            node = window.pick(x, y, pick_flag)
            while node is not None:
                if node is button:
                    return x, y
                node = node.get_parent()
        except (AttributeError, TypeError, RuntimeError):
            continue
    raise RuntimeError("GTK action has no verified hit target; no XTEST click dispatched")
