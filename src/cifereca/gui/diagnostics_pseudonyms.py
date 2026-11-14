"""Compatibility import for GTK Diagnostics.

Implementation and singleton are Core-neutral, so the public Plugin API
never imports cifereca.gui. Settings and the Diagnostics plugin still share
one process-local mapping. Never use this presentation map for raw logs.
"""
from __future__ import annotations

from cifereca.diagnostics_pseudonyms import (
    DiagnosticsPseudonyms, DISPLAY_PSEUDONYMS,
)

__all__ = ["DiagnosticsPseudonyms", "DISPLAY_PSEUDONYMS"]
