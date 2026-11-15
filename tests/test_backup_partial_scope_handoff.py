"""#3196/#5975: partial W1 archives retain scope without fabricating A/B.

A historical checkpoint may carry identity/account metadata; once
canonical rows exist independently, it cannot relabel either owner.
"""
import pytest

from cifereca.storage import Storage


def initial():
    return {
        "provider_id": "seller", "credential_id": "card", "item_id": "edition",
        "identity_id": "A", "account_id": "buyer", "title": "Synthetic book",
        "status": "pending", "acquisition_strategy": "exact-offer",
        "executor": "core", "expires_at": "2099-01-01",
    }


@pytest.mark.parametrize("unknown", [False, True])
def test_partial_attempt_reuses_known_account_and_identity_after_restart(tmp_path, unknown):
    path = tmp_path / "state.sqlite"
    store = Storage(path)
    try:
        store.backup_put(initial())
        first = store.backup_get("seller", "card", "edition")
        assert store.entitlements() == store.acquisitions() == []
        patch = {k: v for k, v in initial().items()
                 if k in ("provider_id", "credential_id", "item_id")}
        if unknown:
            patch.update(account_id=None, identity_id=None)
        patch["status"] = "acquiring"
        store.backup_put(patch)
        archive = store.backup_get("seller", "card", "edition")
        assert archive["status"] == "acquiring"
        assert archive["identity_id"] == "A"
        assert archive["account_id"] == "buyer"
        assert archive["acquisition_strategy"] == "exact-offer"
        assert archive["executor"] == "core"
        assert archive["expires_at"] == "2099-01-01"
        assert first["account_id"] == archive["account_id"]
        assert store.entitlements() == store.acquisitions() == []
    finally:
        store.close()
    reopened = Storage(path)
    try:
        assert reopened.backup_get("seller", "card", "edition")["account_id"] == "buyer"
        assert reopened.acquisitions() == reopened.entitlements() == []
    finally:
        reopened.close()


@pytest.mark.parametrize("table", ["entitlements", "acquisition_ledger"])
def test_partial_update_cannot_evade_canonical_owner_collision(tmp_path, table):
    store = Storage(tmp_path / "state.sqlite")
    try:
        row = initial()
        store.backup_put(row)
        right_id = store.entitlement_put({
            **row, "entitlement_type": "owned", "state": "inactive",
        })
        store.acquisition_put({
            **row, "entitlement_id": right_id,
            "acquisition_id": Storage._acquisition_id_for(row),
            "status": "discovered",
        })
        with store._lock, store._connect() as db:
            db.execute("UPDATE " + table + " SET identity_id='B'")
        before = store.backup_get("seller", "card", "edition")
        rights, receipts = store.entitlements(), store.acquisitions()
        with pytest.raises(ValueError, match="backup_(entitlement|acquisition)_owner_conflict"):
            store.backup_put({
                "provider_id": "seller", "credential_id": "card", "item_id": "edition",
                "status": "acquiring",
            })
        assert store.backup_get("seller", "card", "edition") == before
        assert store.entitlements() == rights and store.acquisitions() == receipts
    finally:
        store.close()
