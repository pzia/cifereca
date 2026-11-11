"""#3196: obsolete Kobo D1 manifest preflight cannot become acquisition authority."""
from pathlib import Path

from cifereca.services import LibraryService
from cifereca.core import kobo_backup


ROOT = Path(__file__).resolve().parents[1] / "src" / "cifereca"


def test_kobo_d1_manifest_preview_engine_is_physically_gone():
    source = (ROOT / "core" / "kobo_backup.py").read_text(encoding="utf-8")
    for name in (
        "_canonical_kobo_backup_offline_preview",
        "_canonical_kobo_offline_historical_report",
        "_canonical_kobo_disconnected_offline_preview",
        "_canonical_kobo_backup_preview",
        "def kobo_backup_preview(",
    ):
        assert name not in source
    assert not hasattr(kobo_backup, "kobo_backup_preview")
    assert not hasattr(LibraryService, "kobo_backup_preview")


def test_private_kobo_preflight_never_refreshes_historical_manifest():
    broker = (ROOT / "brokers.py").read_text(encoding="utf-8")
    assert "self.service.kobo_backup_preview(" not in broker
    cli = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert 'sub.add_parser("kobo-resume-preview"' not in cli
