"""#3196: personal inventory cannot publish Kobo pseudo-rights in Core."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "cifereca"


def test_core_inventory_does_not_dispatch_kobo_entitlement_mirror():
    for path in (ROOT / "services.py", ROOT / "core" / "sync_coordination.py"):
        source = path.read_text(encoding="utf-8")
        assert "mirror_kobo_manifest_rights" not in source
        assert "from .kobo_manifest_rights import" not in source
        assert "from .core.kobo_manifest_rights import" not in source


def test_kobo_legacy_direct_download_is_inert_even_with_owned_flag():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    from cifereca.models import SearchResult
    from cifereca.services import LibraryService

    service = NS(_session_for_source=Mock(side_effect=AssertionError("legacy GET")),
                 current_identity=NS(identity_id="alice"))
    item = SearchResult(
        source_provider="kobo", source_credential="alice-vault",
        record_id="book", title="Synthetic", owned=True,
        downloadable=True,
    )
    assert LibraryService.can_direct_download(service, item) is False
    service._session_for_source.assert_not_called()
