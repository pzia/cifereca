"""#3196: retired Kobo W2 interpretation must stay out of installed Core."""
from __future__ import annotations

from pathlib import Path


def test_kobo_w2_manifest_interpreters_are_removed_from_core():
    root = Path(__file__).resolve().parents[1] / "src/cifereca"
    assert not (root / "core/kobo_backup.py").exists()
    for path in ("core/canonical_selected_edition.py", "core/convergence_execution.py"):
        text = (root / path).read_text(encoding="utf-8")
        for name in ("_kobo_legacy_manifest_claim_invalid", "_kobo_manifest_flags_invalid", "_kobo_manifest_rejection_count", "_manifest_archived"):
            assert name not in text
