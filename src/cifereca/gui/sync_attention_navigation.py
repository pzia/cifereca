"""Safe in-page navigation to actionable Synchronisation rows (#3416).

Navigation is UI-only: no broker operations, account resolution, consent or
provider-specific policies. Targets are freshly built for one scoped page.
"""
from __future__ import annotations

from math import isfinite


def next_sync_attention_target(targets: object, position: object):
    """Cycle through actionable rows, prioritizing errors over warnings.

    No untrusted source ID, label, credential or diagnostics text is used for
    routing. Informational coverage is not a problem to resolve.
    """
    if type(targets) not in (list, tuple) or len(targets) > 1024:
        return None, 0
    ordered = []
    for severity in ("error", "warning"):
        for entry in targets:
            if (type(entry) is tuple and len(entry) == 2
                    and entry[0] == severity and entry[1] is not None):
                ordered.append(entry[1])
    if not ordered:
        return None, 0
    current = position if type(position) is int and 0 <= position < len(ordered) else 0
    return ordered[current], (current + 1) % len(ordered)


def focus_sync_attention_target(scroller: object, target: object) -> bool:
    """Focus one visible Gtk row and scroll its own viewport into view.

    Use Gtk4 widget geometry relative to the scroll content, not a fixed
    position or coordinates from the desktop. Missing/unmapped/stale targets
    fail closed; in particular, do not focus an unrelated account.
    """
    try:
        if (not callable(getattr(scroller, "get_child", None))
                or not callable(getattr(scroller, "get_vadjustment", None))
                or not callable(getattr(target, "compute_bounds", None))
                or not callable(getattr(target, "grab_focus", None))):
            return False
        content = scroller.get_child()
        if content is None:
            return False
        if callable(getattr(target, "get_mapped", None)) and not target.get_mapped():
            return False
        valid, bounds = target.compute_bounds(content)
        if valid is not True or bounds is None:
            return False
        y = float(bounds.get_y())
        adjustment = scroller.get_vadjustment()
        if adjustment is None:
            return False
        lower = float(adjustment.get_lower())
        upper = float(adjustment.get_upper())
        page_size = float(adjustment.get_page_size())
        if not all(map(isfinite, (y, lower, upper, page_size))):
            return False
        if page_size <= 0 or upper < lower:
            return False
        adjustment.set_value(max(lower, min(y - 16.0, upper - page_size)))
        return target.grab_focus() is not False
    except (AttributeError, TypeError, ValueError, RuntimeError):
        # The target might have been unmounted during an asynchronous refresh.
        return False
