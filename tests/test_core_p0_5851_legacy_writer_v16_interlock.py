"""P0 #5851: legacy Paris writer cannot overwrite a live v16 grant."""
from __future__ import annotations

import pytest

from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


SOURCE = dict(provider_id="paris", credential_id="alice-paris", account_id="linked-123")


def _write_old(service, borrower="bob"):
    return service.bind_paris_borrower_adept(
        credential_id="alice-paris", account_id="linked-123",
        borrower_identity_id=borrower, confirmed=True,
    )


def test_legacy_writer_does_not_recreate_second_grant_after_v16_write(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        proof = service.bind_adept_borrower_grant(
            **SOURCE, borrower_identity_id="bob", confirmed=True,
        )
        assert proof["borrower_identity_id"] == "bob"
        with pytest.raises(ValueError, match="requires v16 binding"):
            _write_old(service)
        with pytest.raises(ValueError, match="requires v16 binding"):
            _write_old(service, borrower="alice")
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None
        assert service.storage.adept_borrower_grant_get(**SOURCE) == proof
    finally:
        service.close()


def test_reconfirmation_cannot_be_followed_by_legacy_recreation(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        assert _write_old(service)["borrower_identity_id"] == "bob"
        service.reconfirm_legacy_adept_borrower_grant(
            **SOURCE, borrower_identity_id="bob", confirmed=True,
        )
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None
        with pytest.raises(ValueError, match="requires v16 binding"):
            _write_old(service)
    finally:
        service.close()


def test_legacy_writer_remains_available_for_unmigrated_scope(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        assert service.storage.adept_borrower_grant_get(**SOURCE) is None
        assert _write_old(service)["borrower_identity_id"] == "bob"
    finally:
        service.close()


def test_legacy_writer_fails_closed_if_v16_evidence_lookup_fails(tmp_path, monkeypatch):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        def error(**_scope):
            raise RuntimeError("PRIVATE_GRANT_DATA")

        def forbidden(**_kw):
            pytest.fail("legacy grant must not be written on storage error")

        monkeypatch.setattr(service.storage, "adept_borrower_grant_get", error)
        monkeypatch.setattr(service.storage, "paris_borrower_adept_binding_put", forbidden)
        with pytest.raises(ValueError, match="coordinates invalid") as exc:
            _write_old(service)
        assert "PRIVATE" not in str(exc.value)
    finally:
        service.close()


def test_storage_legacy_writer_rechecks_raw_v16_inside_its_transaction(tmp_path):
    """A direct old Storage API must not recreate a second grant after v16."""
    service, _loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        proof = service.bind_adept_borrower_grant(
            **SOURCE, borrower_identity_id="bob", confirmed=True,
        )
        with pytest.raises(ValueError, match="requires v16 binding"):
            service.storage.paris_borrower_adept_binding_put(
                credential_id="alice-paris", account_id="linked-123",
                borrower_identity_id="bob", profile_id="bob-profile",
                confirmed=True,
            )
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None
        assert service.storage.adept_borrower_grant_get(**SOURCE) == proof
    finally:
        service.close()


def test_v16_created_after_facade_preflight_blocks_legacy_upsert(tmp_path, monkeypatch):
    """Another writer can commit v16 after the facade reads its absence.

    The historical SQLite writer must veto that fresh v16 row under its own
    BEGIN IMMEDIATE lease, rather than trust a stale service-level preflight.
    """
    service, _loan = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        original_get = service.storage.adept_borrower_grant_get
        injected = []

        def race_after_read(**coordinates):
            before = original_get(**coordinates)
            if before is None and not injected:
                service.storage.adept_borrower_grant_put(
                    **SOURCE, connection_identity_id="alice",
                    borrower_identity_id="bob", profile_id="bob-profile",
                    confirmed=True,
                )
                injected.append(True)
            return before

        monkeypatch.setattr(
            service.storage, "adept_borrower_grant_get", race_after_read,
        )
        with pytest.raises(ValueError, match="requires v16 binding"):
            _write_old(service)
        assert injected == [True]
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None
        assert original_get(**SOURCE)["borrower_identity_id"] == "bob"
    finally:
        service.close()
