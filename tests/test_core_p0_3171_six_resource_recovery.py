"""P0 #3171/#2797: 6 synthetic downloadable references, lost local folder, repeat.

The installed reference provider exercises the same V2/Core convergence path
used by acquisition sources; no real credentials, provider network or Adobe.
A valid SQL receipt/SHA is not proof when its original physical file moved.
"""
from pathlib import Path
from types import SimpleNamespace

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_atomic_publication_v14 import register
from tests.test_core_p0_3171_canonical_sync_stage import rig  # dependency for v2
from tests.test_core_p0_3719_v2_execution import v2  # pytest fixture


def test_six_positive_offers_recover_after_library_folder_move(v2, monkeypatch, tmp_path):
    plugin = Path(__file__).resolve().parents[1] / "src" / "plugins" / "provider-reference"
    monkeypatch.syspath_prepend(str(plugin))
    from cifereca_reference import ReferenceSession

    service, storage, root, _calls = v2
    session = ReferenceSession(
        credential_id="offline-card", label="Offline", credentials={},
        options={"scenario": "bulk", "bulk_count": 6,
                 "page_size": 2, "accounts": "one"},
    )
    account = session.connect()[0]
    account.identity_id = "A"
    session.identity_broker = SimpleNamespace(accounts=lambda identity_id=None: (
        SimpleNamespace(
            scope_version=2, provider_id="reference",
            credential_id="offline-card", account_id=account.unique_id,
            identity_id="A",
        ),
    ))
    register(storage, "A", "reference", "offline-card", account=account.unique_id)
    service.account = account
    service.accounts = [account]
    service.session = session
    service._sessions = {
        service._session_key("reference", "offline-card"): session
    }
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    try:
        enabled = broker.set_source_sync_consent(
            sid, enabled=True, expected_revision=None,
        )
        assert enabled["enabled"] is True

        first = synchronize_account_source(service, account)["canonical_execution"]
        assert first["downloaded"] == 6
        assert first["failed"] == first["blocked"] == first["pending"] == 0
        assert session._delivery_calls == 6
        original_placements = storage.placements()
        assert len(original_placements) == 6
        original_paths = tuple(Path(item["path"]) for item in original_placements)
        assert all(path.parent == root / "A" / "Acquisitions"
                   and path.suffix == ".epub" for path in original_paths)
        original_bytes = {path.name: path.read_bytes() for path in original_paths}

        # This is the real P0 reproduction class: SQLite still advertises
        # complete receipt/placement, but none of the six file paths exists.
        moved = tmp_path / "previous-library-physical-folder"
        (root / "A" / "Acquisitions").rename(moved)
        assert not any(path.exists() for path in original_paths)
        assert len(storage.placements()) == 6

        second = synchronize_account_source(service, account)["canonical_execution"]
        assert second["downloaded"] == 6
        assert second["failed"] == second["blocked"] == second["pending"] == 0
        assert session._delivery_calls == 12
        # Historical SQL placements are intentionally preserved for the
        # separate missing-placement repair flow (#2847). They cannot be
        # accepted as currently present, but Sync never silently deletes them.
        all_placements = storage.placements()
        current_paths = tuple(Path(item["path"]) for item in all_placements
                              if Path(item["path"]).is_file())
        assert len(all_placements) == 12
        assert len(current_paths) == 6
        assert {path.read_bytes() for path in current_paths} == set(original_bytes.values())
        assert all(path.parent == root / "A" / "Acquisitions"
                   for path in current_paths)
        assert {path.name: path.read_bytes() for path in moved.iterdir()} == original_bytes

        third = synchronize_account_source(service, account)["canonical_execution"]
        assert third["present_attested"] == 6
        assert third["downloaded"] == 0
        assert session._delivery_calls == 12
        assert len(storage.placements()) == 12  # 6 historical + 6 received
        assert storage.entitlements() == []
    finally:
        session.close()
