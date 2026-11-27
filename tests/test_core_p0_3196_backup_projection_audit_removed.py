"""P0 #3196: the defunct Kobo 0.7 projection audit must stay removed."""
from __future__ import annotations

from pathlib import Path


def test_backup_projection_drift_auditor_was_removed():
    core = Path(__file__).resolve().parents[1] / "src/cifereca/core"
    assert not (core / "backup_projection_audit.py").exists()

