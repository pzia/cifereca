"""P0 #5851: only an explicit, exact-source grant can be written via Core."""
from types import SimpleNamespace as NS

import pytest

from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


def test_service_writes_exact_native_grant_without_promoting_legacy(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    scope = dict(provider_id="paris", credential_id="alice-paris",
                 account_id="linked-123")
    try:
        _persist_account(service.storage)
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert service.storage.adept_borrower_grant_get(**scope) is None
        proof = service.bind_adept_borrower_grant(
            **scope, borrower_identity_id="bob", confirmed=True,
        )
        assert proof["borrower_identity_id"] == "bob"
        assert proof["connection_identity_id"] == "alice"
        assert proof["profile_id"] == "bob-profile"
        assert proof["confirmed_by"] == "explicit"
        assert service.storage.adept_borrower_grant_get(
            provider_id="kobo", credential_id="alice-paris",
            account_id="linked-123",
        ) is None
        service.storage.adept_borrower_grant_delete(**scope)
        assert service.storage.adept_borrower_grant_get(**scope) is None
        # An older Paris grant is neither reinstated nor reimported.
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is not None
    finally:
        service.close()


class Hostile:
    def __str__(self):
        raise AssertionError("untrusted grant coordinate coerced")

    def __bool__(self):
        raise AssertionError("untrusted grant coordinate truth-tested")


class HostileString(str):
    def strip(self):
        raise AssertionError("untrusted source string normalized")


@pytest.mark.parametrize("field,bad", [
    ("provider_id", Hostile()), ("provider_id", HostileString("paris")),
    ("credential_id", Hostile()), ("credential_id", HostileString("alice-paris")),
    ("account_id", Hostile()), ("account_id", HostileString("linked-123")),
    ("borrower_identity_id", Hostile()),
    ("borrower_identity_id", HostileString("bob")),
    ("confirmed", False), ("confirmed", 1), ("confirmed", "true"),
])
def test_service_rejects_malformed_grant_before_storage_write(
    tmp_path, monkeypatch, field, bad,
):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        def forbidden(*args, **kwargs):
            pytest.fail("malformed grant must not touch storage")
        monkeypatch.setattr(service.storage, "adept_borrower_grant_put", forbidden)
        args = dict(provider_id="paris", credential_id="alice-paris",
                    account_id="linked-123", borrower_identity_id="bob",
                    confirmed=True)
        args[field] = bad
        with pytest.raises(ValueError, match="evidence_invalid"):
            service.bind_adept_borrower_grant(**args)
    finally:
        service.close()


def test_service_requires_real_persisted_account_not_just_ui_account(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        # UI account exists but v16 persisted provider_account does not.
        with pytest.raises(ValueError, match="evidence_invalid"):
            service.bind_adept_borrower_grant(
                provider_id="paris", credential_id="alice-paris",
                account_id="linked-123", borrower_identity_id="bob",
                confirmed=True,
            )
        assert service.storage.adept_borrower_grant_get(
            provider_id="paris", credential_id="alice-paris",
            account_id="linked-123",
        ) is None
    finally:
        service.close()


def test_service_refuses_a_revoked_grant_after_persisting_it(tmp_path, monkeypatch):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        _persist_account(service.storage)
        original_get = service.storage.adept_borrower_grant_get
        monkeypatch.setattr(
            service.storage, "adept_borrower_grant_get", lambda **scope: None,
        )
        with pytest.raises(ValueError, match="evidence_invalid"):
            service.bind_adept_borrower_grant(
                provider_id="paris", credential_id="alice-paris",
                account_id="linked-123", borrower_identity_id="bob",
                confirmed=True,
            )
        monkeypatch.setattr(
            service.storage, "adept_borrower_grant_get", original_get,
        )
    finally:
        service.close()
