"""GTK Diagnostics pseudonym compatibility import.

The single session mapping lives in cifereca.gui and is reused by the
Settings Diagnostics preview and the standalone Diagnostics page.
No Core writer, broker, journal or raw export imports this UI-only map.
"""
from cifereca.plugin_api import (
    DiagnosticsPseudonyms, DISPLAY_PSEUDONYMS,
)

__all__ = ["DiagnosticsPseudonyms", "DISPLAY_PSEUDONYMS"]
