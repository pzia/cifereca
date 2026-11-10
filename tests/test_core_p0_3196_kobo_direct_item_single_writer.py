"""#3196: Kobo direct D3 is retired regardless of provider ABI completeness."""
from __future__ import annotations

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _manifest_row, _service


CONTRACTS = (
    ("downloadable_resource_snapshot", "downloadable_selected_offer_snapshot",
     "downloadable_selected_delivery_candidate"),
    ("downloadable_resource_snapshot", "downloadable_selected_offer_snapshot",
     "downloadable_selected_delivery_candidate_guarded"),
    ("downloadable_resource_snapshot", "downloadable_selected_offer_snapshot",
     "downloadable_selected_delivery_candidate_runtime_guarded"),
    ("rights_observation_snapshot", "rights_selected_delivery_offer_snapshot",
     "rights_selected_delivery_candidate"),
)


@pytest.mark.parametrize("contract", CONTRACTS + ((),))
@pytest.mark.parametrize("with_admission", (False, True))
def test_no_direct_kobo_get_with_complete_or_incomplete_abi(
    tmp_path, monkeypatch, contract, with_admission,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    item = session.normalize_item(_manifest_row())
    try:
        for name in contract:
            monkeypatch.setattr(
                session, name,
                lambda *_a, **_kw: pytest.fail("D3 invoked provider ABI"),
                raising=False,
            )
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_a, **_kw: pytest.fail("D3 invoked legacy executor"),
        )
        monkeypatch.setattr(
            session, "download_direct",
            lambda *_a, **_kw: pytest.fail("D3 invoked provider GET"),
        )
        before = (
            service.storage.backup_list(),
            service.storage.acquisitions(provider_id="kobo"),
            service.storage.placements(),
        )
        def admission():
            pytest.fail("retired D3 cannot invoke historical admission")
        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_legacy_direct_get_retired_use_canonical_provider",
        ):
            service.acquire_direct_item(
                item, _admission=admission if with_admission else None,
            )
        assert before == (
            service.storage.backup_list(),
            service.storage.acquisitions(provider_id="kobo"),
            service.storage.placements(),
        )
    finally:
        service.close()
