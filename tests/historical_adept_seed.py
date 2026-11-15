"""Explicit test-only SQL seed for *preexisting* Paris ADEPT legacy rows.

Production no longer writes those rows. Historical reads, v16 reconfirmation,
revocation and rollback tests still need archived database state; constructing
it here cannot create Core authorization or user consent.
"""
from __future__ import annotations

import time


def seed_historical_adept_storage(
    storage, *, credential_id: str, account_id: str,
    borrower_identity_id: str, profile_id: str, confirmed: bool = True,
) -> dict:
    """Simulate an old database row, never exercise a production writer."""
    if (type(credential_id) is not str or not credential_id.strip()
            or type(account_id) is not str
            or type(borrower_identity_id) is not str or not borrower_identity_id
            or type(profile_id) is not str or not profile_id
            or confirmed is not True):
        raise ValueError("historical test seed requires exact coordinates")
    borrower = account_id.strip() or "primary"
    now = int(time.time())
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        connection = db.execute(
            "SELECT identity_id FROM provider_connections "
            "WHERE provider_id='paris' AND credential_id=?",
            (credential_id,),
        ).fetchone()
        identity = db.execute(
            "SELECT 1 FROM identities WHERE identity_id=?",
            (borrower_identity_id,),
        ).fetchone()
        profile = db.execute(
            "SELECT profile_id FROM adept_profile_bindings "
            "WHERE scope_type='identity' AND scope_id=?",
            (borrower_identity_id,),
        ).fetchone()
        if (connection is None or identity is None or profile is None
                or profile[0] != profile_id):
            raise ValueError("historical seed lacks persisted connection/profile")
        # Test state predates the canonical writer, never create dual grants
        # by treating an old fixture as live authorization.
        if db.execute(
            "SELECT 1 FROM adept_borrower_grants WHERE provider_id='paris' "
            "AND credential_id=? AND account_id=?",
            (credential_id, borrower),
        ).fetchone():
            raise ValueError("cannot seed archived row over canonical grant")
        db.execute(
            "INSERT INTO paris_borrower_adept_bindings "
            "(credential_id,account_id,connection_identity_id,"
            "borrower_identity_id,profile_id,confirmed_by,confirmed_at,updated_at) "
            "VALUES(?,?,?,?,?,'explicit',?,?) "
            "ON CONFLICT(credential_id,account_id) DO UPDATE SET "
            "connection_identity_id=excluded.connection_identity_id,"
            "borrower_identity_id=excluded.borrower_identity_id,"
            "profile_id=excluded.profile_id,confirmed_by=excluded.confirmed_by,"
            "confirmed_at=excluded.confirmed_at,updated_at=excluded.updated_at",
            (credential_id, borrower, connection[0], borrower_identity_id,
             profile_id, now, now),
        )
    return storage.paris_borrower_adept_binding_get(
        credential_id=credential_id, account_id=borrower,
    )


def seed_historical_adept_binding(
    service, *, credential_id: str, account_id: str,
    borrower_identity_id: str, confirmed: bool = True,
) -> dict:
    """Service-based convenience for archived borrower fixtures only."""
    profile_id = service.storage.adept_profile_binding_get(
        "identity", borrower_identity_id,
    )
    return seed_historical_adept_storage(
        service.storage, credential_id=credential_id, account_id=account_id,
        borrower_identity_id=borrower_identity_id, profile_id=profile_id,
        confirmed=confirmed,
    )
