"""P0 #3196: Kobo-specific offline analyzer is not shipped as Core.

The diagnostic stays available from repository scripts and does not grant
rights, download files or open the Core runtime.
"""
from __future__ import annotations

from pathlib import Path

from scripts.kobo_final_verdict_summary import classify, main


def test_kobo_final_analyzer_is_outside_installable_core():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "src/cifereca/core/kobo_final_verdict_summary.py").exists()
    assert (root / "scripts/kobo_final_verdict_summary.py").is_file()
    assert classify([])["status"] == "no_instrumented_evidence"
    assert callable(main)
