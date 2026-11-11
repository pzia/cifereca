"""P0 #3196: retired Kobo W2 CLI diagnostics are not a sync authority."""
from pathlib import Path

from cifereca import cli
from cifereca.core.diagnostic_kobo_projection import public_kobo_backup_decision


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca"


def test_retired_kobo_report_commands_and_serializers_are_absent():
    assert not (ROOT / "core/legacy_diagnostic_projection.py").exists()
    assert not (ROOT / "core/kobo_backup.py").exists()
    assert not hasattr(cli, "_kobo_legacy_report_view")
    source = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert "kobo-sync-errors" not in source
    assert "kobo-backup-errors" not in source


def test_current_public_opening_decision_projection_fails_closed():
    private = "Bearer SECRET_PRIVATE from /home/alice/Book.epub"
    assert public_kobo_backup_decision({
        "schema_version": 1, "status": private,
        "credential_id": private, "connected_kobo": 1,
        "eligible_kobo": 1, "interval_minutes": 60,
        "persisted_enabled": False,
    }) == {}
    assert public_kobo_backup_decision({
        "status": "manual_only", "persisted_enabled": False,
        "connected_kobo": 1, "eligible_kobo": 1,
        "interval_minutes": 60, "credential_id": private,
    }) == {
        "operation_kind": "kobo_backup", "phase": "decision",
        "status": "manual_only", "enabled": False,
        "connected": 1, "eligible": 1, "interval_minutes": 60,
    }
