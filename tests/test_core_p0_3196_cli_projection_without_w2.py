"""P0 #3196: the retired W2 CLI report cannot return as another sync authority."""
from pathlib import Path

from cifereca import cli
from cifereca.core.diagnostic_kobo_projection import public_kobo_backup_decision


def test_retired_kobo_report_commands_and_serializers_are_absent():
    assert not hasattr(cli, "_kobo_legacy_report_view")
    assert not (Path(__file__).resolve().parents[1] / "src/cifereca/core/legacy_diagnostic_projection.py").exists()
    src = (Path(__file__).resolve().parents[1] / "src/cifereca/cli.py").read_text(encoding="utf-8")
    assert "kobo-sync-errors" not in src
    assert "kobo-backup-errors" not in src


def test_existing_opening_decision_redaction_remains_closed():
    private = "Bearer SECRET_PRIVATE from /home/alice/Book.epub"
    assert public_kobo_backup_decision({
        "schema_version": 1, "status": private, "credential_id": private,
    }) == {}
