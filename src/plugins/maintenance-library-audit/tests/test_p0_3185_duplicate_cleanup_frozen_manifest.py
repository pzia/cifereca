"""P0 #3185: ordinary duplicate cleanup stays off pending product #4496.

This is a packaging policy, separate from the Core runtime safety hold.
Recovery of a pre-existing tombstone remains an independent operation.
"""
from __future__ import annotations

import json
from pathlib import Path

from cifereca.plugins.manager import _MAINTENANCE_SAFETY_HOLDS


_MANIFEST = Path(__file__).resolve().parents[1] / "cifereca-plugin.json"


def test_destructive_duplicate_cleanup_is_disabled_in_shipped_package():
    plugins = {
        row["id"]: row
        for row in json.loads(_MANIFEST.read_text(encoding="utf-8"))["plugins"]
    }
    cleanup = plugins["maintenance.cleanup.duplicates"]
    recovery = plugins["maintenance.cleanup.duplicates.recover"]

    assert cleanup["enabled_by_default"] is False
    assert cleanup["type"] == "maintenance.tool"
    assert "#4496" in cleanup["help_text"]
    assert "Suspendu" in cleanup["help_text"]
    assert recovery["enabled_by_default"] is True
    assert recovery["entrypoint"] != cleanup["entrypoint"]
    assert plugins["maintenance.audit.integrity"]["enabled_by_default"] is True


def test_default_policy_cannot_override_core_hold_or_activate_cleanup():
    plugins = {
        row["id"]: row
        for row in json.loads(_MANIFEST.read_text(encoding="utf-8"))["plugins"]
    }
    cleanup_id = "maintenance.cleanup.duplicates"
    recovery_id = "maintenance.cleanup.duplicates.recover"
    # The Core manager has a second independent admission hold that defeats
    # manual overrides and force_plugins; covered by its separate CI tests.
    assert _MAINTENANCE_SAFETY_HOLDS.get(cleanup_id) == "#3185"
    assert recovery_id not in _MAINTENANCE_SAFETY_HOLDS
    assert plugins[cleanup_id]["enabled_by_default"] is False
