"""#3196: obsolete Kobo D1 manifest preflight cannot become acquisition authority."""
from pathlib import Path

from cifereca.services import LibraryService


ROOT = Path(__file__).resolve().parents[1] / "src" / "cifereca"


def test_kobo_d1_manifest_preview_engine_is_physically_gone():
    assert not (ROOT / "core" / "kobo_backup.py").exists()
    assert not hasattr(LibraryService, "kobo_backup_preview")


def test_private_kobo_preflight_never_refreshes_historical_manifest():
    broker = (ROOT / "brokers.py").read_text(encoding="utf-8")
    assert "self.service.kobo_backup_preview(" not in broker
    cli = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert 'sub.add_parser("kobo-resume-preview"' not in cli
