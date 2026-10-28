"""P0 #3196: pruning an old Kobo 0.7 projection must not revoke a foreign right.

All operations use synthetic SQLite, no account login, EPUB or HTTP.
The historical compatibility key lacks the Cifereca library identity.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _legacy(*, identity="alice", account="primary", item="book"):
    return {
        "provider_id": "opaque-seller", "credential_id": "card",
        "account_id": account, "identity_id": identity,
        "item_id": item, "entitlement_type": "owned",
        "title": "Synthetic book", "status": "complete",
    }


def _right(storage):
    rights = storage.entitlements(provider_id="opaque-seller")
    assert len(rights) == 1
    return rights[0]


def _register(storage, *, identity="alice", account="primary"):
    storage.identity_put({
        "identity_id": identity, "label": identity, "directory_name": identity,
    })
    connection = storage.provider_connection_put({
        "identity_id": identity, "provider_id": "opaque-seller",
        "credential_id": "card",
    })
    storage.provider_account_put({
        "identity_id": identity, "provider_id": "opaque-seller",
        "credential_id": "card", "connection_id": connection,
        "account_id": account,
    })


def test_same_explicit_right_is_inactivated_only_on_authoritative_prune(tmp_path):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        _register(storage)
        storage.backup_put(_legacy())
        right = _right(storage)
        receipt = storage.acquisitions(provider_id="opaque-seller")
        assert right["state"] == "active"
        assert len(receipt) == 1
        assert storage.backup_prune_missing("opaque-seller", "card", set()) == {
            "pruned": 1, "local_preserved": 0, "entitlements_inactivated": 1,
        }
        after = _right(storage)
        assert after["identity_id"] == "alice"
        assert after["state"] == "inactive"
        assert after["metadata"]["compatibility_projection_pruned"] is True
        assert storage.acquisitions(provider_id="opaque-seller") == receipt
        assert storage.backup_list("opaque-seller") == []
    finally:
        storage.close()


@pytest.mark.parametrize("new_owner", ["bob", None, ""])
def test_legacy_projection_cannot_inactivate_right_reassigned_or_unknown(
    tmp_path, new_owner,
):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        original = _legacy()
        storage.backup_put(original)
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE entitlements SET identity_id=? WHERE entitlement_id=?",
                (new_owner, storage._entitlement_id_for(original)),
            )
        before = _right(storage)
        history = storage.acquisitions(provider_id="opaque-seller")
        assert storage.backup_prune_missing(
            "opaque-seller", "card", set(),
        ) == {
            "pruned": 1, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert _right(storage) == before
        assert storage.acquisitions(provider_id="opaque-seller") == history
        assert storage.backup_list("opaque-seller") == []
    finally:
        storage.close()


@pytest.mark.parametrize("unverified_legacy_identity", [None, ""])
def test_old_projection_without_owner_cannot_revoke_exactly_identified_right(
    tmp_path, unverified_legacy_identity,
):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        storage.backup_put(_legacy())
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE backup_items SET identity_id=? "
                "WHERE provider_id='opaque-seller'",
                (unverified_legacy_identity,),
            )
        before = _right(storage)
        assert storage.backup_prune_missing(
            "opaque-seller", "card", set(),
        )["entitlements_inactivated"] == 0
        assert _right(storage) == before
    finally:
        storage.close()


@pytest.mark.parametrize("changed", [
    {"account_id": "other-borrower"},
    {"account_id": None},
    {"entitlement_type": "loan"},
    {"provider_id": "other-seller"},
    {"credential_id": "another-card"},
    {"item_id": "different-edition"},
])
def test_collision_with_same_entitlement_id_cannot_change_other_source(
    tmp_path, changed,
):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        storage.backup_put(_legacy())
        original = _legacy()
        columns = sorted(changed)
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE entitlements SET "
                + ",".join(f"{key}=?" for key in columns)
                + " WHERE entitlement_id=?",
                (*[changed[key] for key in columns],
                 storage._entitlement_id_for(original)),
            )
        before = storage.entitlements()
        assert storage.backup_prune_missing(
            "opaque-seller", "card", set(),
        )["entitlements_inactivated"] == 0
        assert storage.entitlements() == before
    finally:
        storage.close()


def test_canonical_right_without_legacy_marker_is_never_inactivated(tmp_path):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        storage.backup_put(_legacy())
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE entitlements SET metadata_json='{\"source\":\"canonical\"}'"
            )
        before = _right(storage)
        assert storage.backup_prune_missing(
            "opaque-seller", "card", set(),
        )["entitlements_inactivated"] == 0
        assert _right(storage) == before
    finally:
        storage.close()


def test_manifest_retaining_item_does_not_mark_right_inactive(tmp_path):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        storage.backup_put(_legacy())
        before = _right(storage)
        assert storage.backup_prune_missing(
            "opaque-seller", "card", {"book"},
        ) == {
            "pruned": 0, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert _right(storage) == before
        assert len(storage.backup_list("opaque-seller")) == 1
    finally:
        storage.close()



@pytest.mark.parametrize("seen_ids", [
    None, "book", b"book", {"book": True}, (part for part in ("book",)),
    [None], [123], [""], ["   "], ["book", ""],
    ["book\x00other"], ["book\nother"], ["book\rother"],
    ["x" * 1025], ["book"] * 100_001,
])
def test_malformed_inventory_never_prunes_current_rights_or_history(tmp_path, seen_ids):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        storage.backup_put(_legacy())
        before_backup = storage.backup_list("opaque-seller")
        before_right = storage.entitlements()
        before_receipt = storage.acquisitions(provider_id="opaque-seller")
        with pytest.raises(ValueError, match="invalid_authoritative_manifest_item_ids"):
            storage.backup_prune_missing("opaque-seller", "card", seen_ids)
        assert storage.backup_list("opaque-seller") == before_backup
        assert storage.entitlements() == before_right
        assert storage.acquisitions(provider_id="opaque-seller") == before_receipt
    finally:
        storage.close()


@pytest.mark.parametrize("seen_ids", [
    {"book"}, ["book"], ("book",), (" book ",),
])
def test_valid_complete_inventory_preserves_exact_legacy_right(tmp_path, seen_ids):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        storage.backup_put(_legacy())
        assert storage.backup_prune_missing("opaque-seller", "card", seen_ids) == {
            "pruned": 0, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert _right(storage)["state"] == "active"
    finally:
        storage.close()



def test_stale_right_is_not_inactivated_after_credential_rebind(tmp_path):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        _register(storage, identity="alice", account="primary")
        storage.backup_put(_legacy())
        before_right = _right(storage)
        receipt = storage.acquisitions(provider_id="opaque-seller")
        storage.identity_put({
            "identity_id": "bob", "label": "Bob", "directory_name": "bob",
        })
        # Simulate the documented rebind in the same SQLite transaction:
        # no supplier manifest for Bob can attest Alice's right as absent.
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE provider_connections SET identity_id='bob' "
                "WHERE provider_id='opaque-seller' AND credential_id='card'"
            )
            db.execute(
                "DELETE FROM provider_accounts "
                "WHERE provider_id='opaque-seller' AND credential_id='card'"
            )
            connection_id = db.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='opaque-seller' AND credential_id='card'"
            ).fetchone()[0]
        storage.provider_account_put({
            "identity_id": "bob", "provider_id": "opaque-seller",
            "credential_id": "card", "connection_id": connection_id,
            "account_id": "primary",
        })
        result = storage.backup_prune_missing("opaque-seller", "card", set())
        assert result["entitlements_inactivated"] == 0
        assert _right(storage) == before_right
        assert storage.acquisitions(provider_id="opaque-seller") == receipt
        assert storage.backup_list("opaque-seller") == []
    finally:
        storage.close()


def test_stale_right_is_not_inactivated_after_borrower_rebind(tmp_path):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        _register(storage, identity="alice", account="primary")
        storage.backup_put(_legacy())
        before = _right(storage)
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE provider_accounts SET account_id='secondary' "
                "WHERE identity_id='alice' AND provider_id='opaque-seller' "
                "AND credential_id='card'"
            )
        result = storage.backup_prune_missing("opaque-seller", "card", set())
        assert result["pruned"] == 1
        assert result["entitlements_inactivated"] == 0
        assert _right(storage) == before
    finally:
        storage.close()


def test_missing_registered_source_is_not_negative_evidence_for_a_right(tmp_path):
    storage = Storage(tmp_path / "test.sqlite")
    try:
        storage.backup_put(_legacy())
        before = _right(storage)
        result = storage.backup_prune_missing("opaque-seller", "card", set())
        assert result == {
            "pruned": 1, "local_preserved": 0, "entitlements_inactivated": 0,
        }
        assert _right(storage) == before
    finally:
        storage.close()
