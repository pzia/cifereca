"""P0 #3196: no supplier-specific orphan counter in the installed Core."""
from pathlib import Path

from cifereca.services import LibraryService
from cifereca.core import kobo_backup


def test_retired_kobo_orphan_counter_is_physically_absent():
    assert not hasattr(LibraryService, "kobo_unlinked_local_count")
    assert not hasattr(kobo_backup, "kobo_unlinked_local_count")
    assert not hasattr(kobo_backup, "_attested_received_kobo_copy")
    assert not hasattr(kobo_backup, "_verified_canonical_kobo_unlinked_exemptions")
    root = Path(__file__).resolve().parents[1]
    services = (root / "src/cifereca/services.py").read_text(encoding="utf-8")
    assert "def kobo_unlinked_local_count(" not in services
