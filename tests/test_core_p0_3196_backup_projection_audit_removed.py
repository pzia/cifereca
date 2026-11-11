"""P0 #3196: the defunct Kobo 0.7 projection audit must stay removed."""
from __future__ import annotations

from pathlib import Path

from cifereca.core import source_convergence


def test_backup_projection_drift_auditor_was_removed():
    core = Path(__file__).resolve().parents[1] / "src/cifereca/core"
    assert not (core / "backup_projection_audit.py").exists()


def test_canonical_source_convergence_remains_available():
    assert source_convergence.SourceKey is not None
