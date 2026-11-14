"""P0 #5851: explicit ADEPT v16 reconfirmation permanently retires legacy authority.

SQLite tombstones are independent of the active v16 grant: revocation,
connection/profile rotation, process restarts and direct legacy writers cannot
bring back a retired binding. Unmigrated grants remain usable.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.storage import Storage, CURRENT_SCHEMA_VERSION
from tests.test_core_p0_5851_atomic_legacy_grant_reconfirmation import (
    setup_legacy, scope, legacy, grant,
)
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


def _retired(store):
    with store._lock, store._connect() as db:
        return db.execute(
            "SELECT retired_at FROM paris_adept_grant_retirements "
            "WHERE credential_id=? AND account_id=?",
            ("alice-paris", "linked-123"),
        ).fetchone()


def _write_legacy(store):
    store.paris_borrower_adept_binding_put(
        credential_id="alice-paris", account_id="linked-123",
        borrower_identity_id="bob", profile_id="bob-profile", confirmed=True,
    )


def test_v17_reconfirmation_is_monotone_across_revoke_and_reopen(tmp_path):
    service, loan = setup_legacy(tmp_path)
    db_path = service.storage.path
    try:
        assert service.storage.schema_version() == CURRENT_SCHEMA_VERSION == 17
        assert _retired(service.storage) is None
        service.reconfirm_legacy_adept_borrower_grant(
            **scope(), borrower_identity_id="bob", confirmed=True,
        )
        assert _retired(service.storage) is not None
        assert legacy(service) is None
        assert grant(service) is not None
        service.storage.adept_borrower_grant_delete(**scope())
        assert grant(service) is None
        with pytest.raises(sqlite3.IntegrityError, match="adept_legacy_grant_retired"):
            _write_legacy(service.storage)
        assert legacy(service) is None
    finally:
        service.close()

    reopened = Storage(db_path)
    try:
        assert reopened.schema_version() == 17
        assert _retired(reopened) is not None
        with pytest.raises(sqlite3.IntegrityError, match="adept_legacy_grant_retired"):
            _write_legacy(reopened)
        with reopened._lock, reopened._connect() as db:
            with pytest.raises(sqlite3.IntegrityError, match="adept_legacy_grant_retired"):
                db.execute(
                    "INSERT INTO paris_borrower_adept_bindings "
                    "(credential_id,account_id,connection_identity_id,"
                    "borrower_identity_id,profile_id,confirmed_by,confirmed_at,updated_at) "
                    "VALUES (?,?,?,?,?,'explicit',1,1)",
                    ("alice-paris", "linked-123", "alice", "bob", "bob-profile"),
                )
        assert _retired(reopened) is not None
    finally:
        reopened.close()


def test_failed_reconfirmation_does_not_leave_tombstone(tmp_path):
    service, _ = setup_legacy(tmp_path)
    try:
        with service.storage._lock, service.storage._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_retirement
                BEFORE DELETE ON paris_borrower_adept_bindings
                BEGIN SELECT RAISE(ABORT, 'simulated failure'); END
            """)
        with pytest.raises(sqlite3.DatabaseError):
            service.reconfirm_legacy_adept_borrower_grant(
                **scope(), borrower_identity_id="bob", confirmed=True,
            )
        assert _retired(service.storage) is None
        assert legacy(service) is not None
        assert grant(service) is None
    finally:
        service.close()


def test_unmigrated_legacy_is_not_tombstoned_and_can_update(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        _write_legacy(service.storage)
        assert _retired(service.storage) is None
        assert legacy(service)["borrower_identity_id"] == "bob"
        _write_legacy(service.storage)
        assert legacy(service)["borrower_identity_id"] == "bob"
    finally:
        service.close()


def test_tombstone_is_exact_to_credential_and_account(tmp_path):
    service, _ = setup_legacy(tmp_path)
    try:
        service.reconfirm_legacy_adept_borrower_grant(
            **scope(), borrower_identity_id="bob", confirmed=True,
        )
        service.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "paris",
            "credential_id": "other-paris", "label": "Other source",
        })
        service.storage.paris_borrower_adept_binding_put(
            credential_id="other-paris", account_id="linked-123",
            borrower_identity_id="bob", profile_id="bob-profile", confirmed=True,
        )
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="other-paris", account_id="linked-123",
        ) is not None
        assert _retired(service.storage) is not None
    finally:
        service.close()


def test_direct_legacy_update_into_retired_key_is_rejected(tmp_path):
    service, _ = setup_legacy(tmp_path)
    try:
        service.reconfirm_legacy_adept_borrower_grant(
            **scope(), borrower_identity_id="bob", confirmed=True,
        )
        service.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "paris",
            "credential_id": "other-paris", "label": "Other source",
        })
        service.storage.paris_borrower_adept_binding_put(
            credential_id="other-paris", account_id="linked-123",
            borrower_identity_id="bob", profile_id="bob-profile", confirmed=True,
        )
        with service.storage._lock, service.storage._connect() as db:
            with pytest.raises(sqlite3.IntegrityError, match="adept_legacy_grant_retired"):
                db.execute(
                    "UPDATE paris_borrower_adept_bindings "
                    "SET credential_id='alice-paris' "
                    "WHERE credential_id='other-paris' AND account_id='linked-123'"
                )
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="other-paris", account_id="linked-123",
        ) is not None
    finally:
        service.close()
