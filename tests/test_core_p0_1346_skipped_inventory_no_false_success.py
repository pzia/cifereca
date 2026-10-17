"""#1346/#1905: ignored source refresh is never a successful inventory."""
from __future__ import annotations

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from tests.test_core_p0_1346_inventory_refresh_broker import _rig
from tests.test_core_p0_1905_numilog_inventory_schedule import _numilog


@pytest.mark.parametrize("provider", ["kobo", "numilog"])
@pytest.mark.parametrize("prior_success", [False, True])
def test_skipped_provider_result_never_advances_private_inventory_evidence(
    tmp_path, provider, prior_success,
):
    service, account, _session, calls = (
        _rig(tmp_path) if provider == "kobo" else _numilog(tmp_path)
    )
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        if prior_success:
            assert broker.synchronize_source(sid).ok
            assert len(calls) == 1
        previous = broker._state(sid)
        assert bool(previous.get("inventory_last_success")) is prior_success
        assert bool(previous.get("last_success")) is prior_success

        # Simulate a source disabled between the broker's initial admission
        # and the actual Core source operation. No provider/network activity.
        def revoked(_account, **_kwargs):
            service.storage.metadata_set("sync.source.enabled." + sid, "0")
            return {
                "status": "skipped", "reason": "source_disabled",
                "library": 49,  # A contradictory private payload is not success.
                "owned_rights": 47,
                "provider": "PRIVATE_CREDENTIAL_SHOULD_NOT_LEAK",
            }

        service.synchronize_account_source = revoked
        receipt = broker.synchronize_source(sid)

        assert receipt.ok and receipt.changed == 0
        assert receipt.facts == {"status": "skipped", "skipped": True}
        assert "PRIVATE_CREDENTIAL" not in repr(receipt)
        final = broker._state(sid)
        assert final["status"] == "skipped"
        assert final["last_attempt"]
        assert final.get("last_success", "") == previous.get("last_success", "")
        for key in (
            "inventory_last_attempt", "inventory_status", "inventory_last_success",
        ):
            assert final.get(key) == previous.get(key)
        assert broker._enabled(sid) is False
        assert len(calls) == int(prior_success)
    finally:
        service.storage.close()
