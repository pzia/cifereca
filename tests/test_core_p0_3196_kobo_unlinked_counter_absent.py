"""P0 #3196: no supplier-specific orphan counter in the installed Core."""
from pathlib import Path

from cifereca.services import LibraryService


def test_retired_kobo_orphan_counter_is_physically_absent():
    assert not hasattr(LibraryService, "kobo_unlinked_local_count")
    root = Path(__file__).resolve().parents[1]
    assert not (root / "src/cifereca/core/kobo_backup.py").exists()
    services = (root / "src/cifereca/services.py").read_text(encoding="utf-8")
    assert "def kobo_unlinked_local_count(" not in services
