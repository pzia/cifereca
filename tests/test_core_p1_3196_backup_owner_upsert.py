"""#3196: legacy Kobo projection cannot silently reassign canonical owners.

Real SQLite and synthetic local rows; no account login, network or files.
"""
from __future__ import annotations

import pytest
from cifereca.storage import Storage


def _row(*, owner="alice", borrower="reader", item="book", title="Original"):
    return {
        "provider_id": "kobo", "credential_id": "card-a",
        "identity_id": owner, "account_id": borrower, "item_id": item,
        "entitlement_type": "owned", "status": "pending",
        "title": title,
    }


def _state(storage):
    return {
        "backup": storage.backup_list("kobo"),
        "rights": storage.entitlements(),
        "receipts": storage.acquisitions(provider_id="kobo"),
    }


@pytest.mark.parametrize("change", ["identity", "account", "entitlement"])
def test_legacy_key_collision_rejects_scope_reassignment_atomically(tmp_path, change):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        first = _row()
        storage.backup_put(first)
        before = _state(storage)
        new = {**first, "title": "Foreign update"}
        if change == "identity":
            new["identity_id"] = "bob"
        elif change == "account":
            new["account_id"] = "other-reader"
        else:
            new["entitlement_type"] = "loan"
        with pytest.raises(ValueError, match="backup_projection_owner_conflict"):
            storage.backup_put(new)
        assert _state(storage) == before
    finally:
        storage.close()


@pytest.mark.parametrize("table", ["entitlements", "acquisition_ledger"])
def test_authoritative_owner_conflict_rolls_back_compatibility_write(
    tmp_path, table,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        first = _row()
        storage.backup_put(first)
        with storage._lock, storage._connect() as db:
            db.execute(
                f"UPDATE {table} SET identity_id='bob' "
                "WHERE provider_id='kobo' AND credential_id='card-a'"
            )
        before = _state(storage)
        with pytest.raises(ValueError, match=(
            "backup_entitlement_owner_conflict" if table == "entitlements"
            else "backup_acquisition_owner_conflict"
        )):
            storage.backup_put({**first, "title": "Unattested reassignment"})
        assert _state(storage) == before
    finally:
        storage.close()


def test_exact_owner_retry_still_updates_legacy_and_generic_projection(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        first = _row()
        storage.backup_put(first)
        storage.backup_put({**first, "status": "complete", "title": "Updated"})
        result = _state(storage)
        assert result["backup"][0]["status"] == "complete"
        assert result["rights"][0]["state"] == "active"
        assert result["receipts"][0]["status"] == "complete"
        assert result["receipts"][0]["identity_id"] == "alice"
        assert len(result["rights"]) == len(result["receipts"]) == 1
    finally:
        storage.close()


def test_unknown_legacy_identity_is_not_transferred_to_different_known_owner(
    tmp_path,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        first = _row(owner=None)
        storage.backup_put(first)
        storage.backup_put(_row(owner="alice", title="Bound"))
        state = _state(storage)
        assert state["backup"][0]["identity_id"] == "alice"
        assert state["rights"][0]["identity_id"] == "alice"
        assert state["receipts"][0]["identity_id"] == "alice"
        assert len(state["backup"]) == 1
    finally:
        storage.close()


def test_repeating_known_row_without_identity_does_not_unbind_canonical_owner(
    tmp_path,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        storage.backup_put(_row())
        storage.backup_put(_row(owner=None, title="Legacy retry"))
        state = _state(storage)
        assert state["backup"][0]["identity_id"] == "alice"
        assert state["rights"][0]["identity_id"] == "alice"
        assert state["receipts"][0]["identity_id"] == "alice"
    finally:
        storage.close()
