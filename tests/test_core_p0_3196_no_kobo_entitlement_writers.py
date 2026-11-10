"""#3196: personal inventory cannot publish Kobo pseudo-rights in Core."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "cifereca"


def test_core_inventory_does_not_dispatch_kobo_entitlement_mirror():
    for path in (ROOT / "services.py", ROOT / "core" / "sync_coordination.py"):
        source = path.read_text(encoding="utf-8")
        assert "mirror_kobo_manifest_rights" not in source
        assert "mirror_kobo_manifest_rights(" not in source


def test_kobo_legacy_direct_download_is_inert_even_with_owned_flag():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    from tests.test_core_1337_kobo_personal_manifest_rights import _row
    from cifereca.services import LibraryService

    service = NS(_session_for_source=Mock(side_effect=AssertionError("legacy GET")),
                 current_identity=NS(identity_id="alice"))
    item = _row(product="book", credential="alice-vault")
    assert LibraryService.can_direct_download(service, item) is False
    service._session_for_source.assert_not_called()
