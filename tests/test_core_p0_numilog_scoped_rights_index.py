"""#1905: Numilog purchase reconciliation must scale by account, not DB size."""
from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from cifereca.acquisition_engine import Entitlement, SourceIdentity, stable_id
from cifereca.core.numilog_manifest_rights import mirror_numilog_manifest_rights
from cifereca.storage import Storage
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_sync_state_contract import _service


def _foreign(storage, entitlement_id, item_id, credential, account_id):
    storage.entitlement_put({
        "entitlement_id": entitlement_id, "provider_id": "numilog",
        "credential_id": credential, "account_id": account_id,
        "identity_id": "alex", "item_id": item_id,
        "entitlement_type": "owned", "state": "active",
        "metadata": {"title": "SECRET_OTHER_IDENTITY"},
    })


@pytest.mark.parametrize("n", [1, 310])
def test_numilog_rights_mirror_queries_only_current_scope_and_candidate_ids(
    tmp_path, monkeypatch, n,
):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)
        # Foreign rights are legitimate storage rows; their metadata may
        # never need to be decoded or enumerated to mirror this account.
        for i in range(65):
            item = "unrelated-%d" % i
            _foreign(
                service.storage, "foreign-owned-%d" % i, item,
                "other-vault", "other-account",
            )
        rows = [_row(account.credential_id, i) for i in range(n)]
        monkeypatch.setattr(
            service.storage, "entitlements",
            lambda **_kw: pytest.fail("global rights enumeration forbidden"),
        )
        statements = []
        db = service.storage._connect()
        db.set_trace_callback(statements.append)
        try:
            count = mirror_numilog_manifest_rights(
                service, rows, identity_id=account.identity_id,
                credential_id=account.credential_id,
                account_id=account.unique_id,
            )
        finally:
            db.set_trace_callback(None)
        assert count == n
        assert all("metadata_json" not in sql for sql in statements
                   if "SELECT" in sql and "entitlements" in sql)
        assert any("AND identity_id=" in sql and "AND account_id=" in sql
                   for sql in statements)
        assert any("entitlement_id IN (" in sql for sql in statements)
        # 310 rows = 620 candidate IDs; bounded 300-ID chunks, no N+1 reads.
        lookups = [sql for sql in statements if "entitlement_id IN (" in sql]
        assert len(lookups) == (2 * n + 299) // 300
        assert calls == []
    finally:
        service.close()


def test_foreign_collision_uses_scoped_fallback_without_reassigning_owner(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)
        row = _row(account.credential_id, 0)
        source = SourceIdentity(
            "numilog", account.credential_id, account.unique_id,
            account.identity_id,
        )
        canonical = Entitlement(source=source, item_id=row.record_id).entitlement_id
        _foreign(
            service.storage, canonical, row.record_id,
            account.credential_id, account.unique_id,
        )
        for _ in range(2):
            assert mirror_numilog_manifest_rights(
                service, [row], identity_id=account.identity_id,
                credential_id=account.credential_id,
                account_id=account.unique_id,
            ) == 1
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 2
        assert {r["identity_id"] for r in rights} == {"alex", "default"}
        foreign = next(r for r in rights if r["identity_id"] == "alex")
        own = next(r for r in rights if r["identity_id"] == "default")
        assert foreign["entitlement_id"] == canonical
        assert foreign["metadata"]["title"] == "SECRET_OTHER_IDENTITY"
        key = (account.identity_id, account.credential_id,
               account.unique_id, row.record_id)
        assert own["entitlement_id"] == stable_id("numilog-owned-identity", *key)
        assert own["metadata"]["source"] == "numilog.authenticated_library"
        assert calls == []
    finally:
        service.close()


def test_double_foreign_collision_refuses_to_overwrite_fallback(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)
        row = _row(account.credential_id, 0)
        source = SourceIdentity("numilog", account.credential_id,
                                account.unique_id, account.identity_id)
        key = (account.identity_id, account.credential_id,
               account.unique_id, row.record_id)
        _foreign(service.storage,
                 Entitlement(source=source, item_id=row.record_id).entitlement_id,
                 row.record_id, account.credential_id, account.unique_id)
        _foreign(service.storage, stable_id("numilog-owned-identity", *key),
                 row.record_id, account.credential_id, account.unique_id)
        assert mirror_numilog_manifest_rights(
            service, [row], identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id,
        ) == 0
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 2
        assert all(r["identity_id"] == "alex"
                   and r["metadata"]["title"] == "SECRET_OTHER_IDENTITY"
                   for r in rights)
        assert calls == []
    finally:
        service.close()


def test_lazy_numilog_inventory_rechecks_binding_before_any_right_write(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)

        def old_owner_pages():
            yield _row(account.credential_id, 0)
            # Credential is reassigned while the next page is yielded.
            service.storage.provider_connection_put({
                "provider_id": "numilog", "credential_id": account.credential_id,
                "identity_id": "alex", "enabled": True,
            })
            yield _row(account.credential_id, 1)

        assert mirror_numilog_manifest_rights(
            service, old_owner_pages(), identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id,
        ) == 0
        assert service.storage.entitlements(provider_id="numilog") == []
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("invalid", [
    ("", "vault", "account", ()),
    ("alice", "vault", "account", ("",)),
    ("alice", "vault", "account", ("valid-id", None)),
    ("alice", "vault", "account", ["valid-id"]),
    ("alice\nSECRET", "vault", "account", ()),
])
def test_numilog_scope_index_rejects_invalid_inputs_without_sql(tmp_path, invalid):
    storage = Storage(tmp_path / "numilog-index.sqlite3")
    try:
        seen = []
        storage._connect().set_trace_callback(seen.append)
        with pytest.raises(ValueError, match="^invalid_numilog_entitlement_scope$"):
            storage.numilog_entitlement_reservations(
                identity_id=invalid[0], credential_id=invalid[1],
                account_id=invalid[2], candidate_ids=invalid[3],
            )
        assert seen == []
    finally:
        storage.close()
