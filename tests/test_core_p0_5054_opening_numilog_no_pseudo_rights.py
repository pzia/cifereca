"""P0 #5054: opening personal inventory must never project Numilog pseudo-rights."""
from tests.test_core_p0_1862_numilog_store_mirror import _connect
from tests.test_sync_state_contract import _service


def test_opening_private_numilog_inventory_is_metadata_only(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=2)
        first = service.mirror_personal_libraries()
        second = service.mirror_personal_libraries()
        assert first == {f"numilog:{account.credential_id}": 2}
        assert second == first
        assert calls == ["library", "library"]
        assert len(service.storage.catalog_entries()) == 2
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.acquisitions(provider_id="numilog") == []
        assert service.storage.placements() == []
        assert not list((tmp_path / "EBooks").rglob("*.epub"))
        assert not list((tmp_path / "EBooks").rglob("*.acsm"))
    finally:
        service.close()
