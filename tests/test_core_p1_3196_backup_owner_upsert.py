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




def test_stale_passive_replay_keeps_independently_completed_canonical_receipt(
    tmp_path,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        first = _row()
        storage.backup_put(first)
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET status='complete',"
                " artifact_id=?, error_code='canonical_observed_failure' "
                "WHERE acquisition_id=?",
                ("sha256:" + "a" * 64, storage._acquisition_id_for(first)),
            )
        for _ in range(2):
            # A new passive provider inventory still advertises an old
            # pending compatibility state; it is not an acquisition event.
            storage.backup_put({**first, "title": "Replayed catalogue state"})
            canonical = storage.acquisitions(provider_id="kobo")[0]
            assert canonical["status"] == "complete"
            assert canonical["artifact_id"] == "sha256:" + "a" * 64
            assert canonical["error_code"] == "canonical_observed_failure"
            assert storage.backup_list("kobo")[0]["status"] == "pending"
        assert len(storage.entitlements()) == 1
    finally:
        storage.close()


def test_existing_canonical_complete_stays_complete_during_first_old_projection(
    tmp_path,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        row = _row()
        canonical_id = storage._acquisition_id_for(row)
        storage.acquisition_put({
            "acquisition_id": canonical_id,
            "entitlement_id": storage._entitlement_id_for(row),
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "card-a", "account_id": "reader",
            "item_id": "book", "title": "Direct Core acquisition",
            "status": "complete",
        })
        storage.backup_put(row)
        assert storage.acquisitions(provider_id="kobo")[0]["status"] == "complete"
        assert storage.backup_list("kobo")[0]["status"] == "pending"
        assert len(storage.entitlements()) == 1
    finally:
        storage.close()


@pytest.mark.parametrize("passive_status", ["pending", "queued", "unknown"])
def test_passive_legacy_replay_cannot_poison_any_completed_receipt_field(
    tmp_path, passive_status,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        row = _row()
        storage.backup_put(row)
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET status='complete',"
                "strategy='canonical_selected_delivery',executor='Core',"
                "attempt_count=7,last_attempt_at=1800000123,"
                "artifact_id=?,error_code='canonical_receipt',"
                "error_message='trusted-status',"
                "attention_code='trusted-attention',updated_at=1800000999 "
                "WHERE acquisition_id=?",
                ("sha256:" + "a" * 64, storage._acquisition_id_for(row)),
            )
        receipt = storage.acquisitions(provider_id="kobo")[0]
        assert receipt["strategy"] == "canonical_selected_delivery"
        assert receipt["status"] == "complete"
        assert receipt["updated_at"] == 1800000999
        for replay in range(2):
            storage.backup_put({
                **row, "status": passive_status,
                "acquisition_strategy": "stale_unverified_strategy",
                "executor": "legacy_opaque_executor",
                "attempt_count": 100 + replay,
                "last_attempt_at": 1900000000 + replay,
                "error": "stale delivery failure",
                "attention_code": "stale attention",
                "updated_at": 2000000000 + replay,
            })
            # Only the compatibility projection advances, never the
            # independently completed receipt or its visible chronology.
            assert storage.acquisitions(provider_id="kobo")[0] == receipt
        assert storage.backup_list("kobo")[0]["status"] == passive_status
    finally:
        storage.close()


def test_active_legacy_restart_after_completion_keeps_forward_updates(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        row = {**_row(), "status": "complete"}
        storage.backup_put(row)
        storage.backup_put({
            **row, "status": "acquiring",
            "acquisition_strategy": "explicit_new_attempt",
            "executor": "kobo_adapter",
            "attempt_count": 3,
            "last_attempt_at": 1900000001,
            "updated_at": 1900000002,
        })
        receipt = storage.acquisitions(provider_id="kobo")[0]
        assert receipt["status"] == "acquiring"
        assert receipt["strategy"] == "explicit_new_attempt"
        assert receipt["executor"] == "kobo_adapter"
        assert receipt["attempt_count"] == 3
        assert receipt["last_attempt_at"] == 1900000001
        assert receipt["updated_at"] == 1900000002
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


def test_active_retry_after_completed_kobo_receipt_is_not_intercepted(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        row = {**_row(), "status": "complete"}
        storage.backup_put(row)
        storage.backup_put({**row, "status": "acquiring"})
        assert storage.acquisitions(provider_id="kobo")[0]["status"] == "acquiring"
        storage.backup_put({**row, "status": "attention_required"})
        assert storage.acquisitions(provider_id="kobo")[0]["status"] == "attention_required"
    finally:
        storage.close()



@pytest.mark.parametrize("canonical_state", ["active", "inactive"])
def test_legacy_replay_never_replaces_attested_canonical_entitlement(
    tmp_path, canonical_state,
):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        row = _row()
        canonical_id = storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "card-a",
            "account_id": "reader", "identity_id": "alice",
            "item_id": "book", "entitlement_type": "owned",
            "revision_id": "provider-revision", "state": canonical_state,
            "metadata": {
                "source": "attested_personal_inventory",
                "proof": "fresh-positive-right",
            },
        })
        before_rights = storage.entitlements()
        assert len(before_rights) == 1
        assert before_rights[0]["entitlement_id"] == canonical_id
        for status in ("pending", "complete", "pending"):
            storage.backup_put({
                **row, "status": status, "revision_id": "stale-legacy",
                "title": "Legacy backup item",
            })
            assert storage.entitlements() == before_rights
        assert storage.backup_list("kobo")[0]["revision_id"] == "stale-legacy"
        assert len(storage.acquisitions(provider_id="kobo")) == (
            1 if canonical_state == "active" else 0
        )
        if canonical_state == "inactive":
            assert storage.artifacts() == []
            assert storage.placements() == []
    finally:
        storage.close()


def test_legacy_projection_cannot_replace_unmarked_canonical_metadata(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    try:
        row = _row()
        storage.backup_put(row)
        rights = storage.entitlements()
        assert len(rights) == 1
        original = rights[0]
        storage.entitlement_put({
            "entitlement_id": original["entitlement_id"],
            "provider_id": "kobo", "credential_id": "card-a",
            "account_id": "reader", "identity_id": "alice",
            "item_id": "book", "entitlement_type": "owned",
            "revision_id": "canonical-later",
            "state": "inactive", "metadata": {
                "source": "provider_attested_later",
            },
        })
        before = storage.entitlements()
        storage.backup_put({**row, "status": "complete", "revision_id": "stale"})
        assert storage.entitlements() == before
        assert storage.backup_list("kobo")[0]["status"] == "complete"
    finally:
        storage.close()



@pytest.mark.parametrize("legacy_status", [
    "pending", "complete", "acquiring", "attention_required",
])
def test_independently_inactive_right_prevents_all_passive_legacy_canonical_writes(
    tmp_path, legacy_status,
):
    """Preserve the 0.7 history but never materialize an acquisition for revoked ownership."""
    import hashlib

    storage = Storage(tmp_path / "revoked.sqlite3")
    try:
        row = {
            **_row(), "status": legacy_status, "revision_id": "legacy-revision",
            "local_path": str(tmp_path / "historic.epub"),
            "sha256": hashlib.sha256(b"historical bytes").hexdigest(),
            "size": len(b"historical bytes"),
            "format": "epub",
        }
        # Even a physically present historical file does not grant a
        # provider right or provenance after an independent revocation.
        (tmp_path / "historic.epub").write_bytes(b"historical bytes")
        canonical_id = storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "card-a",
            "account_id": "reader", "identity_id": "alice",
            "item_id": "book", "entitlement_type": "owned",
            "revision_id": "attested-provider-revision",
            "state": "inactive", "metadata": {"source": "fresh-provider-right"},
        })
        before_rights = storage.entitlements()
        assert before_rights[0]["entitlement_id"] == canonical_id
        with storage._lock, storage._connect() as db:
            before = {name: db.execute("SELECT * FROM " + name).fetchall()
                      for name in ("entitlements", "acquisition_ledger",
                                   "artifacts", "placements", "catalog_entries",
                                   "source_references", "origin_links")}
        for attempt in range(2):
            storage.backup_put({**row, "title": "Legacy replay " + str(attempt)})
            assert storage.entitlements() == before_rights
            with storage._lock, storage._connect() as db:
                for name, rows in before.items():
                    assert db.execute("SELECT * FROM " + name).fetchall() == rows
        backup = storage.backup_get("kobo", "card-a", "book")
        assert backup["status"] == legacy_status
        assert backup["title"] == "Legacy replay 1"
        assert backup["local_path"] == str(tmp_path / "historic.epub")
        assert (tmp_path / "historic.epub").read_bytes() == b"historical bytes"
    finally:
        storage.close()


def test_independently_revoked_right_legacy_replay_cannot_reopen_completed_receipt(
    tmp_path,
):
    import hashlib

    storage = Storage(tmp_path / "existing.sqlite3")
    try:
        row = {
            **_row(), "status": "complete",
            "sha256": hashlib.sha256(b"unverified legacy").hexdigest(),
            "local_path": str(tmp_path / "obsolete.epub"),
            "size": len(b"unverified legacy"), "format": "epub",
        }
        entitlement_id = storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "card-a",
            "account_id": "reader", "identity_id": "alice",
            "item_id": "book", "entitlement_type": "owned",
            "revision_id": "canonical", "state": "inactive",
            "metadata": {"attested": True},
        })
        storage.acquisition_put({
            "acquisition_id": storage._acquisition_id_for(row),
            "entitlement_id": entitlement_id,
            "provider_id": "kobo", "credential_id": "card-a",
            "identity_id": "alice", "account_id": "reader",
            "item_id": "book", "status": "attention_required",
            "metadata": {"provenance": "canonical-provider"},
        })
        receipts = storage.acquisitions(provider_id="kobo")
        storage.backup_put(row)
        assert storage.acquisitions(provider_id="kobo") == receipts
        assert storage.backup_get("kobo", "card-a", "book")["status"] == "complete"
        assert storage.artifacts() == []
        assert storage.placements() == []
    finally:
        storage.close()
