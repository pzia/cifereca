"""Compatibility imports for the former Kobo Settings module.

Provider-neutral CoreUI no longer owns these provider-specific widgets.
Keep the old import path while legacy tests and external callers migrate.
"""
from __future__ import annotations

from .settings_legacy_kobo_section import (
    KoboSyncSettings,
    build_kobo_sync_section,
    kobo_backup_action_state,
)

__all__ = [
    "KoboSyncSettings",
    "build_kobo_sync_section",
    "kobo_backup_action_state",
]
