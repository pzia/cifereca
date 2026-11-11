"""P0 #3196: the retired W2 CLI report cannot return as another sync authority."""
from pathlib import Path

from cifereca import cli
from cifereca.core import legacy_diagnostic_projection as projection


def test_retired_kobo_report_commands_and_serializers_are_absent():
    assert not hasattr(cli, "_kobo_legacy_report_view")
    assert not hasattr(projection, "safe_kobo_backup_report")
    assert not hasattr(projection, "_safe_kobo_backup_diagnostic_detail")
    src = (Path(__file__).resolve().parents[1] / "src/cifereca/cli.py").read_text(encoding="utf-8")
    assert "kobo-sync-errors" not in src
    assert "kobo-backup-errors" not in src


def test_existing_opening_decision_redaction_remains_closed():
    private = "Bearer SECRET_PRIVATE from /home/alice/Book.epub"
    assert projection._safe_kobo_opening_decision({
        "schema_version": 1, "status": private, "credential_id": private,
    }) == {"schema_version": 1, "status": "unknown", "persisted_enabled": False}
