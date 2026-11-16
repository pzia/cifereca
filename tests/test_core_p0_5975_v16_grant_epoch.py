"""P0 #5975: durable, source-scoped v16 grant epoch anti-ABA.

Only explicit grants are positive authority. The monotonic generation is a
necessary revocation veto, not a replacement for borrower/profile attestation.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.storage import Storage
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _scope(provider="paris"):
    return dict(provider_id=provider, credential_id="alice-paris",
                account_id="linked-123")


def _put(store, provider="paris"):
    store.adept_borrower_grant_put(
        **_scope(provider), connection_identity_id="alice",
        borrower_identity_id="bob", profile_id="bob-profile", confirmed=True,
    )


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
def test_reconfirmation_and_delete_recreate_retire_previous_grant_epoch(
    tmp_path, provider,
):
    service, _ = service_with_linked_borrower(tmp_path)
    store = service.storage
    try:
        if provider != "paris":
            store.provider_connection_put({
                "provider_id": provider, "credential_id": "alice-paris",
                "identity_id": "alice", "enabled": True,
            })
        _persist_account(store, provider=provider)
        coords = _scope(provider)
        assert store.adept_borrower_grant_epoch(**coords) is None
        _put(store, provider)
        first = store.adept_borrower_grant_epoch(**coords)
        assert type(first) is int and first >= 1
        assert store.adept_borrower_grant_epoch_current(**coords, epoch=first) is True
        # Even a same-second reconfirmation of identical fields must retire
        # the earlier positive proof (timestamp equality is insufficient).
        _put(store, provider)
        second = store.adept_borrower_grant_epoch(**coords)
        assert type(second) is int and second > first
        assert store.adept_borrower_grant_epoch_current(**coords, epoch=first) is False
        store.adept_borrower_grant_delete(**coords)
        assert store.adept_borrower_grant_epoch(**coords) is None
        assert store.adept_borrower_grant_epoch_current(**coords, epoch=second) is False
        _put(store, provider)
        third = store.adept_borrower_grant_epoch(**coords)
        assert type(third) is int and third > second
        assert store.adept_borrower_grant_epoch_current(**coords, epoch=second) is False
        assert store.adept_borrower_grant_epoch_current(**coords, epoch=third) is True
    finally:
        service.close()


def test_raw_sqlite_grant_writes_rotate_epoch_and_tombstone_cannot_rewind(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    store = service.storage
    try:
        _persist_account(store)
        _put(store)
        coords = _scope()
        old = store.adept_borrower_grant_epoch(**coords)
        assert type(old) is int
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE adept_borrower_grants SET updated_at=updated_at "
                "WHERE provider_id=? AND credential_id=? AND account_id=?",
                tuple(coords.values()),
            )
        current = store.adept_borrower_grant_epoch(**coords)
        assert current > old
        with store._lock, store._connect() as db:
            with pytest.raises(sqlite3.IntegrityError, match="rewind"):
                db.execute(
                    "UPDATE adept_borrower_grant_epochs SET epoch=? "
                    "WHERE provider_id=? AND credential_id=? AND account_id=?",
                    (old, *coords.values()),
                )
            with pytest.raises(sqlite3.IntegrityError, match="retirement"):
                db.execute(
                    "DELETE FROM adept_borrower_grant_epochs "
                    "WHERE provider_id=? AND credential_id=? AND account_id=?",
                    tuple(coords.values()),
                )
        assert store.adept_borrower_grant_epoch_current(**coords, epoch=current)
    finally:
        service.close()


@pytest.mark.parametrize("invalid", [None, False, True, 0, -1, 1.0, "1", object()])
def test_epoch_check_rejects_non_native_and_invalid_values(tmp_path, invalid):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        assert service.storage.adept_borrower_grant_epoch_current(
            **_scope(), epoch=invalid,
        ) is False
    finally:
        service.close()


def test_restart_and_schema17_reopen_preserve_grant_epoch(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    store = service.storage
    path = store.path
    try:
        _persist_account(store)
        _put(store)
        old = store.adept_borrower_grant_epoch(**_scope())
        assert type(old) is int
        assert store.schema_version() == 17
    finally:
        service.close()
    reopened = Storage(path)
    try:
        assert reopened.schema_version() == 17
        assert reopened.adept_borrower_grant_epoch(**_scope()) == old
        _put(reopened)
        new = reopened.adept_borrower_grant_epoch(**_scope())
        assert new > old
        assert not reopened.adept_borrower_grant_epoch_current(
            **_scope(), epoch=old,
        )
    finally:
        reopened.close()


def test_provider_scopes_are_independent_with_identical_credential_and_account(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    store = service.storage
    try:
        store.provider_connection_put({
            "provider_id": "synthetic-pnb", "credential_id": "alice-paris",
            "identity_id": "alice", "enabled": True,
        })
        for provider in ("paris", "synthetic-pnb"):
            _persist_account(store, provider=provider)
            _put(store, provider)
        first = store.adept_borrower_grant_epoch(**_scope("paris"))
        other = store.adept_borrower_grant_epoch(**_scope("synthetic-pnb"))
        store.adept_borrower_grant_delete(**_scope("paris"))
        assert store.adept_borrower_grant_epoch(**_scope("paris")) is None
        assert store.adept_borrower_grant_epoch_current(
            **_scope("synthetic-pnb"), epoch=other,
        )
        _put(store, "paris")
        assert store.adept_borrower_grant_epoch(**_scope("paris")) > first
    finally:
        service.close()


def test_profile_rotation_revokes_grant_and_keeps_epoch_tombstone(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    store = service.storage
    try:
        _persist_account(store)
        _put(store)
        old = store.adept_borrower_grant_epoch(**_scope())
        store.adept_profile_binding_set("identity", "bob", "rotated-profile")
        assert store.adept_borrower_grant_epoch(**_scope()) is None
        store.adept_profile_binding_set("identity", "bob", "bob-profile")
        _put(store)
        assert store.adept_borrower_grant_epoch(**_scope()) > old
        assert not store.adept_borrower_grant_epoch_current(**_scope(), epoch=old)
    finally:
        service.close()
