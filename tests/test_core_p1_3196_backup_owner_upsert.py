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



@pytest.mark.parametrize("canonical_status", ["complete", "attention_required", "retryable_error"])
def test_legacy_replay_cannot_rewind_independently_advanced_canonical_status(
    tmp_path, canonical_status,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        first = _row()
        storage.backup_put(first)
        # The Core acquisition may progress without writing the v0.7
        # compatibility projection. That divergence is real migration data,
        # not an invitation for a subsequent backup replay to overwrite it.
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET status=?,"
                " error_code='canonical_observed_failure',"
                " attention_code='canonical_needs_review'"
                " WHERE acquisition_id=?",
                (canonical_status, storage._acquisition_id_for(first)),
            )
        before = _state(storage)
        with pytest.raises(ValueError, match="backup_canonical_status_diverged"):
            storage.backup_put({**first, "title": "Stale inventory refresh"})
        assert _state(storage) == before
        # A second replay is idempotently refused, not a silent downgrade.
        with pytest.raises(ValueError, match="backup_canonical_status_diverged"):
            storage.backup_put(first)
        assert _state(storage) == before
    finally:
        storage.close()


def test_canonical_only_acquisition_cannot_be_taken_over_by_first_legacy_projection(
    tmp_path,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        row = _row()
        canonical_id = storage._acquisition_id_for(row)
        storage.acquisition_put({
            "acquisition_id": canonical_id,
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "card-a", "account_id": "reader",
            "item_id": "book", "title": "Direct Core acquisition",
            "status": "complete",
        })
        prior = storage.acquisitions(provider_id="kobo")
        assert not storage.backup_list("kobo")
        with pytest.raises(ValueError, match="backup_canonical_status_diverged"):
            storage.backup_put({**row, "title": "Unverified v0.7 backfill"})
        assert storage.acquisitions(provider_id="kobo") == prior
        assert storage.backup_list("kobo") == []
        assert storage.entitlements() == []
    finally:
        storage.close()


@pytest.mark.parametrize("next_status", ["acquiring", "attention_required", "complete"])
def test_normal_same_owner_backup_progress_still_updates_canonical_receipt(
    tmp_path, next_status,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        row = _row()
        storage.backup_put(row)
        storage.backup_put({**row, "status": next_status})
        assert storage.acquisitions(provider_id="kobo")[0]["status"] == next_status
        assert storage.backup_list("kobo")[0]["status"] == next_status
        assert len(storage.entitlements()) == 1
    finally:
        storage.close()
