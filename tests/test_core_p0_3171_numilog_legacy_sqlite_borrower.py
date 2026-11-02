"""P0 #3171: legacy Numilog inventory/preflight requires the real Core account.

A connected session plus a credential's enabled provider connection is NOT
proof that this specific borrower is still attached to that connection. All
tests are local SQLite v14, no private HTTP or book delivery.
"""
from cifereca.core.numilog_manifest_rights import (
    mirror_numilog_manifest_rights, numilog_inventory_binding_valid,
)
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_sync_state_contract import _service


def test_legacy_numilog_borrower_must_be_registered_not_just_connected(tmp_path):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        assert numilog_inventory_binding_valid(service, account, session=session)
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "DELETE FROM provider_accounts WHERE identity_id=? "
                "AND provider_id='numilog' AND credential_id=? "
                "AND account_id=?", (
                    account.identity_id, account.credential_id, account.unique_id,
                ),
            )
        assert not numilog_inventory_binding_valid(service, account, session=session)
        assert mirror_numilog_manifest_rights(
            service, [_row(account.credential_id, 0)],
            identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id,
        ) == 0
        assert service.storage.entitlements(provider_id="numilog") == []
        assert calls == []
    finally:
        service.close()


def test_foreign_connection_cannot_claim_borrower_amid_100_siblings(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        provider, credential, owner = (
            account.provider_id, account.credential_id, account.identity_id,
        )
        current = next(
            c["connection_id"] for c in service.storage.provider_connections(
                identity_id=owner,
            ) if c["provider_id"] == provider and c["credential_id"] == credential
        )
        for n in range(100):
            service.storage.provider_account_put({
                "connection_id": current, "identity_id": owner,
                "provider_id": provider, "credential_id": credential,
                "account_id": f"sibling-{n}", "label": "Synthetic sibling",
            })
        other = service.storage.provider_connection_put({
            "provider_id": "unrelated", "credential_id": "foreign-card",
            "identity_id": owner, "enabled": True,
        })
        # An old registry walk or identity_for_connection() would still
        # accept the exact textual borrower; the current-connection join
        # must reject it. Forbid falling back to those old enumeration APIs.
        monkeypatch.setattr(
            service.storage, "provider_connections",
            lambda **kw: (_ for _ in ()).throw(
                AssertionError("unbounded provider registry scan")
            ),
        )
        monkeypatch.setattr(
            service.storage, "identity_for_connection",
            lambda *_: (_ for _ in ()).throw(
                AssertionError("separate unpinned owner read")
            ),
        )
        assert numilog_inventory_binding_valid(service, account, session=session)
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id=? AND provider_id='numilog' "
                "AND credential_id=? AND account_id=?",
                (other, owner, credential, account.unique_id),
            )
        assert not numilog_inventory_binding_valid(service, account, session=session)
        assert mirror_numilog_manifest_rights(
            service, [_row(account.credential_id, 0)],
            identity_id=owner, credential_id=credential,
            account_id=account.unique_id,
        ) == 0
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id=? AND provider_id='numilog' "
                "AND credential_id=? AND account_id=?",
                (current, owner, credential, account.unique_id),
            )
        assert numilog_inventory_binding_valid(service, account, session=session)
        assert calls == []
    finally:
        service.close()
