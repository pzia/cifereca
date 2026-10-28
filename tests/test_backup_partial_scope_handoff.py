"""#3196: partial legacy updates preserve the known canonical source scope."""
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
        entitlement_id = store.entitlements()[0]["entitlement_id"]
        acquisition_id = store.acquisitions()[0]["acquisition_id"]
        patch = {k: v for k, v in initial().items() if k in ("provider_id", "credential_id", "item_id")}
        if unknown:
            patch.update(account_id=None, identity_id=None)
        patch["status"] = "acquiring"
        store.backup_put(patch)
        assert len(store.entitlements()) == len(store.acquisitions()) == 1
        right, receipt = store.entitlements()[0], store.acquisitions()[0]
        assert right["entitlement_id"] == entitlement_id
        assert receipt["acquisition_id"] == acquisition_id
        for record in (right, receipt, store.backup_get("seller", "card", "edition")):
            assert record["identity_id"] == "A" and record["account_id"] == "buyer"
        assert receipt["status"] == "acquiring"
        assert receipt["strategy"] == "exact-offer"
        assert receipt["executor"] == "core"
        assert right["expires_at"] == "2099-01-01"
    finally:
        store.close()
    reopened = Storage(path)
    try:
        assert len(reopened.acquisitions()) == len(reopened.entitlements()) == 1
        assert reopened.acquisitions()[0]["account_id"] == "buyer"
    finally:
        reopened.close()


@pytest.mark.parametrize("table", ["entitlements", "acquisition_ledger"])
def test_partial_update_cannot_evade_canonical_owner_collision(tmp_path, table):
    store = Storage(tmp_path / "state.sqlite")
    try:
        store.backup_put(initial())
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
