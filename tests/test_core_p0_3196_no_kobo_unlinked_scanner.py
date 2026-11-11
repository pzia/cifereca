"""#3196: legacy Kobo-only orphan accounting is not a Core sync gate.

Canonical SourceReference/OriginLink/Artifact/Placement validation is covered
by the provider-neutral publication and materialization suites.
"""
from pathlib import Path

from cifereca.services import LibraryService
from cifereca.core import kobo_backup


def test_legacy_kobo_orphan_preflight_is_physically_absent():
    assert not hasattr(LibraryService, "kobo_unlinked_local_count")
    for name in (
        "kobo_unlinked_local_count",
        "_attested_received_kobo_copy",
        "_verified_canonical_kobo_unlinked_exemptions",
    ):
        assert not hasattr(kobo_backup, name), name

    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/core/kobo_backup.py").read_text(encoding="utf-8")
    assert "def kobo_unlinked_local_count(" not in source
    assert "backup_items" not in source[source.find("def kobo_backup_diagnostic("):]
