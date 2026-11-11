"""P0 #3196: retired Kobo W2 CLI diagnostics are not a sync authority."""
from pathlib import Path

from cifereca import cli


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca"


def test_retired_kobo_report_commands_and_serializers_are_absent():
    assert not (ROOT / "core/legacy_diagnostic_projection.py").exists()
    assert not (ROOT / "core/kobo_backup.py").exists()
    assert not hasattr(cli, "_kobo_legacy_report_view")
    source = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert "kobo-sync-errors" not in source
    assert "kobo-backup-errors" not in source



def test_retired_vendor_diagnostic_projection_module_is_absent():
    assert not (ROOT / "core/diagnostic_kobo_projection.py").exists()
