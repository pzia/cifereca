"""GTK-safe adapter for explicit Browser Recorder Core diagnostics (#2663).

Use the attested Core method, never the generic Diagnostics.add() channel.
Exceptions/URLs/proxy text never reach the collector, even when a worker fails.
"""
from __future__ import annotations


def emit_browser_recorder_diagnostic(window: object, operation: str, code: str) -> None:
    """Record one typed outcome, leaving GTK behavior unaffected on failures."""
    try:
        diagnostics = getattr(window, "diagnostics", None)
        if diagnostics is None:
            return
        add = getattr(diagnostics, "add_core_browser_recorder_result", None)
        if callable(add):
            add(operation, code)
    except Exception:
        # Even a faulty optional collector must never stringify exceptions.
        return
