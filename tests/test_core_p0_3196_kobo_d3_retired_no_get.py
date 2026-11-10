"""P0 #3196/#4858: retired direct Kobo D3 is never an acquisition path."""
from __future__ import annotations

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _manifest_row, _service


@pytest.mark.parametrize("historical_status", [None, "pending", "complete", "acquiring"])
@pytest.mark.parametrize("with_admission", [False, True])
def test_retired_d3_fails_closed_without_provider_get_or_file_changes(
    tmp_path, monkeypatch, historical_status, with_admission,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    item = session.normalize_item(_manifest_row())
    try:
        if historical_status is not None:
            service.storage.backup_put({
                "provider_id": "kobo", "credential_id": "vault-1",
                "identity_id": "alice", "account_id": "account",
                "item_id": "book-1", "revision_id": "historical",
                "title": "Historical", "entitlement_type": "owned",
                "status": historical_status,
                "local_path": str(tmp_path / "keep.epub"),
            })
        existing = tmp_path / "keep.epub"
        existing.write_bytes(b"existing-local-file")
        before = (
            service.storage.backup_list(),
            service.storage.acquisitions(provider_id="kobo"),
            service.storage.placements(),
        )
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_a, **_kw: pytest.fail("legacy provider GET is prohibited"),
        )
        monkeypatch.setattr(
            session, "download_direct",
            lambda *_a, **_kw: pytest.fail("direct provider GET is prohibited"),
        )
        def unexpected_admission():
            pytest.fail("D3 must not request fresh authorization or revive W2")

        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_legacy_direct_get_retired_use_canonical_provider",
        ):
            service._acquire_direct_item_impl(
                item, _admission=unexpected_admission if with_admission else None,
            )
        assert existing.read_bytes() == b"existing-local-file"
        assert before == (
            service.storage.backup_list(),
            service.storage.acquisitions(provider_id="kobo"),
            service.storage.placements(),
        )
    finally:
        service.close()
