"""#1120: an explicit Paris/ADEPT borrower grant needs exact native evidence."""
from __future__ import annotations

import pytest

from tests.historical_adept_seed import seed_historical_adept_binding
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


PRIVATE = "https://private.invalid/borrow.acsm?token=NEVER_EXPOSE"


class Hostile:
    def __bool__(self):
        raise AssertionError(PRIVATE)

    def __str__(self):
        raise AssertionError(PRIVATE)

    def __eq__(self, _other):
        raise AssertionError(PRIVATE)


class HostileString(str):
    def __eq__(self, _other):
        raise AssertionError(PRIVATE)

    def __str__(self):
        raise AssertionError(PRIVATE)


class HostileGrant(dict):
    def get(self, _key, *_a):
        raise AssertionError(PRIVATE)


@pytest.mark.parametrize("key,bad,expected", [
    ("connection_identity_id", Hostile(), "adept_borrower_connection_changed"),
    ("connection_identity_id", HostileString("alice"), "adept_borrower_connection_changed"),
    ("connection_identity_id", 1, "adept_borrower_connection_changed"),
    ("borrower_identity_id", Hostile(), "adept_borrower_profile_changed"),
    ("borrower_identity_id", HostileString("bob"), "adept_borrower_profile_changed"),
    ("borrower_identity_id", True, "adept_borrower_profile_changed"),
    ("profile_id", Hostile(), "adept_borrower_profile_changed"),
    ("profile_id", HostileString("bob-profile"), "adept_borrower_profile_changed"),
    ("profile_id", None, "adept_borrower_profile_changed"),
    ("confirmed_by", Hostile(), "adept_borrower_profile_changed"),
    ("confirmed_by", HostileString("explicit"), "adept_borrower_profile_changed"),
    ("confirmed_by", True, "adept_borrower_profile_changed"),
])
def test_untrusted_grant_fields_never_attest_borrower(
    tmp_path, monkeypatch, key, bad, expected,
):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        grant = {
            "connection_identity_id": "alice",
            "borrower_identity_id": "bob",
            "profile_id": "bob-profile",
            "confirmed_by": "explicit",
        }
        grant[key] = bad
        monkeypatch.setattr(
            service.storage, "paris_borrower_adept_binding_get",
            lambda **_kw: grant,
        )
        resolved = service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        )
        assert resolved == (None, expected)
        assert PRIVATE not in repr(resolved)
        assert service.storage.acquisitions(provider_id="paris") == []
    finally:
        service.close()


def test_untrusted_grant_mapping_getter_is_never_called(tmp_path, monkeypatch):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        monkeypatch.setattr(
            service.storage, "paris_borrower_adept_binding_get",
            lambda **_kw: HostileGrant(),
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_profile_changed")
    finally:
        service.close()


@pytest.mark.parametrize("bad", [Hostile(), HostileString("bob-profile"), True, []])
def test_untrusted_current_profile_binding_invalidates_grant(tmp_path, monkeypatch, bad):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        seed_historical_adept_binding(service, 
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        monkeypatch.setattr(
            service.storage, "adept_profile_binding_get",
            lambda *_a: bad,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_profile_changed")
    finally:
        service.close()


def test_historical_grant_is_readable_but_not_live_borrower_authority(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
        seed_historical_adept_binding(service, 
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
    finally:
        service.close()
