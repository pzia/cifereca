"""P0 #3196: obsolete W4 reconciliation CLI cannot be invoked or reinstated."""
from pathlib import Path


def test_retired_w4_command_is_absent_but_canonical_relink_remains():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/cifereca/cli.py").read_text(encoding="utf-8")
    assert '"kobo-reconcile-local"' not in source
    assert '"kobo-relink-canonical"' in source
    assert 'reconcile_existing_profile(' in source
