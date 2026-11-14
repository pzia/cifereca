"""P0 #5851: native generic ADEPT grant register, never legacy promotion."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from cifereca.storage import Storage
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _persist_account(store, *, provider="paris", credential="alice-paris"):
    with store._lock, store._connect() as db:
        rows = db.execute(
            "SELECT connection_id FROM provider_connections "
            "WHERE provider_id=? AND credential_id=?",
            (provider, credential),
        ).fetchall()
    assert len(rows) == 1
    store.provider_account_put({
        "connection_id": rows[0][0], "provider_id": provider,
        "credential_id": credential, "identity_id": "alice",
        "account_id": "linked-123", "is_primary": False,
    })


def test_generic_grant_is_new_explicit_proof_not_legacy_projection(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    store = service.storage
    try:
        _persist_account(store)
        assert store.schema_version() == 17
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        coords = dict(provider_id="paris", credential_id="alice-paris",
                      account_id="linked-123")
        assert store.adept_borrower_grant_get(**coords) is None
        store.adept_borrower_grant_put(
            **coords, connection_identity_id="alice",
            borrower_identity_id="bob", profile_id="bob-profile",
            confirmed=True,
        )
        result = store.adept_borrower_grant_get(**coords)
        assert result["borrower_identity_id"] == "bob"
        assert result["connection_identity_id"] == "alice"
        assert result["profile_id"] == "bob-profile"
        assert result["confirmed_by"] == "explicit"
        assert store.adept_borrower_grant_get(
            provider_id="kobo", credential_id="alice-paris",
            account_id="linked-123",
        ) is None
        # Revoking the scoped grant does not revive or copy the legacy row.
        store.adept_borrower_grant_delete(**coords)
        assert store.adept_borrower_grant_get(**coords) is None
        assert store.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is not None
    finally:
        service.close()


def test_generic_grant_isolated_by_provider_with_same_credential_and_account(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    store = service.storage
    try:
        store.provider_connection_put({
            "provider_id": "kobo", "credential_id": "alice-paris",
            "identity_id": "alice", "label": "Synthetic separate provider",
        })
        _persist_account(store, provider="paris")
        _persist_account(store, provider="kobo")
        base = dict(credential_id="alice-paris", account_id="linked-123",
                    connection_identity_id="alice",
                    borrower_identity_id="bob", profile_id="bob-profile",
                    confirmed=True)
        store.adept_borrower_grant_put(provider_id="paris", **base)
        store.adept_borrower_grant_put(provider_id="kobo", **base)
        paris = store.adept_borrower_grant_get(
            provider_id="paris", credential_id="alice-paris",
            account_id="linked-123")
        assert paris is not None
        store.adept_borrower_grant_delete(
            provider_id="paris", credential_id="alice-paris",
            account_id="linked-123")
        assert store.adept_borrower_grant_get(
            provider_id="paris", credential_id="alice-paris",
            account_id="linked-123") is None
        assert store.adept_borrower_grant_get(
            provider_id="kobo", credential_id="alice-paris",
            account_id="linked-123") is not None
    finally:
        service.close()


def test_profile_rotation_and_connection_rebinding_invalidate_generic_grant(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    store = service.storage
    coords = dict(provider_id="paris", credential_id="alice-paris",
                  account_id="linked-123")
    try:
        _persist_account(store)
        store.adept_borrower_grant_put(
            **coords, connection_identity_id="alice",
            borrower_identity_id="bob", profile_id="bob-profile", confirmed=True)
        assert store.adept_borrower_grant_get(**coords) is not None
        store.adept_profile_binding_set("identity", "bob", "new-profile")
        assert store.adept_borrower_grant_get(**coords) is None
        # Rotating back never resurrects the old explicit confirmation.
        store.adept_profile_binding_set("identity", "bob", "bob-profile")
        assert store.adept_borrower_grant_get(**coords) is None
        store.adept_profile_binding_set("identity", "bob", "new-profile")
        with pytest.raises(ValueError, match="evidence_invalid"):
            store.adept_borrower_grant_put(
                **coords, connection_identity_id="alice",
                borrower_identity_id="bob", profile_id="bob-profile", confirmed=True)
    finally:
        service.close()


@pytest.mark.parametrize("field,bad", [
    ("provider_id", None), ("provider_id", 1),
    ("provider_id", " paris"), ("credential_id", ""),
    ("credential_id", True), ("account_id", object()),
    ("account_id", " linked-123"), ("confirmed", False),
    ("confirmed", 1), ("confirmed", "true"),
])
def test_malformed_generic_grant_never_writes(tmp_path, field, bad):
    service, _ = service_with_linked_borrower(tmp_path)
    store = service.storage
    coords = dict(provider_id="paris", credential_id="alice-paris",
                  account_id="linked-123", connection_identity_id="alice",
                  borrower_identity_id="bob", profile_id="bob-profile",
                  confirmed=True)
    try:
        coords[field] = bad
        with pytest.raises(ValueError):
            store.adept_borrower_grant_put(**coords)
        with store._lock, store._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM adept_borrower_grants").fetchone()[0] == 0
    finally:
        service.close()


def test_v15_upgrade_creates_empty_grants_without_copying_legacy(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    dbpath = service.storage.path
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True)
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DROP TABLE adept_borrower_grants")
            db.execute("UPDATE metadata SET value='15' WHERE key='schema_version'")
    finally:
        service.close()
    upgraded = Storage(dbpath)
    try:
        assert upgraded.schema_version() == 17
        assert upgraded.adept_borrower_grant_get(
            provider_id="paris", credential_id="alice-paris",
            account_id="linked-123") is None
        assert upgraded.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123") is not None
    finally:
        upgraded.close()


def test_connection_reassignment_revokes_grant_even_after_owner_restoration(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    store = service.storage
    coords = dict(provider_id="paris", credential_id="alice-paris",
                  account_id="linked-123")
    try:
        _persist_account(store)
        store.adept_borrower_grant_put(
            **coords, connection_identity_id="alice",
            borrower_identity_id="bob", profile_id="bob-profile",
            confirmed=True,
        )
        assert store.adept_borrower_grant_get(**coords) is not None
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE provider_connections SET identity_id='bob' "
                "WHERE provider_id='paris' AND credential_id='alice-paris'"
            )
            db.execute(
                "UPDATE provider_connections SET identity_id='alice' "
                "WHERE provider_id='paris' AND credential_id='alice-paris'"
            )
        assert store.adept_borrower_grant_get(**coords) is None
    finally:
        service.close()


def test_disabled_connection_revokes_grant_without_reenabling_it(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    store = service.storage
    coords = dict(provider_id="paris", credential_id="alice-paris",
                  account_id="linked-123")
    try:
        _persist_account(store)
        store.adept_borrower_grant_put(
            **coords, connection_identity_id="alice",
            borrower_identity_id="bob", profile_id="bob-profile",
            confirmed=True,
        )
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE provider_connections SET enabled=0 "
                "WHERE provider_id='paris' AND credential_id='alice-paris'"
            )
        assert store.adept_borrower_grant_get(**coords) is None
        with pytest.raises(ValueError, match="evidence_invalid"):
            store.adept_borrower_grant_put(
                **coords, connection_identity_id="alice",
                borrower_identity_id="bob", profile_id="bob-profile",
                confirmed=True,
            )
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE provider_connections SET enabled=1 "
                "WHERE provider_id='paris' AND credential_id='alice-paris'"
            )
        assert store.adept_borrower_grant_get(**coords) is None
    finally:
        service.close()
