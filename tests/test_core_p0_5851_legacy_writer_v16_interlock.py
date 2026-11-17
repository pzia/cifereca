"""P0 #5975: retired Paris ADEPT writers are inert before/after v16.

Archived rows and the SQL v17 retirement trigger stay operational, while
neither live Core API is allowed to create a new v15 authority.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.historical_adept_seed import seed_historical_adept_binding
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


SOURCE = dict(provider_id="paris", credential_id="alice-paris", account_id="linked-123")


def _write_old(service, borrower="bob"):
    return service.bind_paris_borrower_adept(
        credential_id="alice-paris", account_id="linked-123",
        borrower_identity_id=borrower, confirmed=True,
    )


def _old_storage_put(storage, credential_id="alice-paris"):
    storage.paris_borrower_adept_binding_put(
        credential_id=credential_id, account_id="linked-123",
        borrower_identity_id="bob", profile_id="bob-profile", confirmed=True,
    )


def test_public_legacy_writer_is_closed_even_on_unmigrated_scope(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        assert service.storage.adept_borrower_grant_get(**SOURCE) is None
        with pytest.raises(ValueError, match="retired"):
            _write_old(service)
        with pytest.raises(ValueError, match="retired"):
            _old_storage_put(service.storage)
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None
    finally:
        service.close()


def test_legacy_writer_cannot_recreate_grant_after_v16_write_or_revoke(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        proof = service.bind_adept_borrower_grant(
            **SOURCE, borrower_identity_id="bob", confirmed=True,
        )
        for borrower in ("bob", "alice"):
            with pytest.raises(ValueError, match="retired"):
                _write_old(service, borrower)
        with pytest.raises(ValueError, match="retired"):
            _old_storage_put(service.storage)
        assert service.storage.adept_borrower_grant_get(**SOURCE) == proof
        service.storage.adept_borrower_grant_delete(**SOURCE)
        with pytest.raises(ValueError, match="retired"):
            _old_storage_put(service.storage)
        assert service.storage.adept_borrower_grant_get(**SOURCE) is None
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None
    finally:
        service.close()


def test_reconfirmed_archived_row_stays_retired_in_live_core(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        historical = seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert historical["borrower_identity_id"] == "bob"
        service.reconfirm_legacy_adept_borrower_grant(
            **SOURCE, borrower_identity_id="bob", confirmed=True,
        )
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None
        with pytest.raises(ValueError, match="retired"):
            _write_old(service)
        with pytest.raises(ValueError, match="retired"):
            _old_storage_put(service.storage)
        # Raw SQL still obeys the v17 no-resurrection tombstone.
        with service.storage._lock, service.storage._connect() as db:
            with pytest.raises(sqlite3.IntegrityError, match="adept_legacy_grant_retired"):
                db.execute(
                    "INSERT INTO paris_borrower_adept_bindings "
                    "(credential_id,account_id,connection_identity_id,"
                    "borrower_identity_id,profile_id,confirmed_by,confirmed_at,updated_at)"
                    " VALUES(?,?,?,?,?,'explicit',1,1)",
                    ("alice-paris", "linked-123", "alice", "bob", "bob-profile"),
                )
    finally:
        service.close()


def test_retired_writer_does_not_read_mutable_v16_or_reach_sqlite(tmp_path, monkeypatch):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        def forbidden(*_args, **_kwargs):
            raise AssertionError("retired writer touched SQLite or grant cache")
        monkeypatch.setattr(service.storage, "adept_borrower_grant_get", forbidden)
        monkeypatch.setattr(service.storage, "_connect", forbidden)
        with pytest.raises(ValueError, match="retired") as exc:
            _write_old(service)
        with pytest.raises(ValueError, match="retired"):
            _old_storage_put(service.storage)
        assert "PRIVATE" not in str(exc.value)
    finally:
        # Restore the DB seam before service.close() flushes its own state.
        monkeypatch.undo()
        service.close()
