"""#1120: an explicit Paris/ADEPT borrower grant needs exact native evidence."""
from __future__ import annotations

import pytest

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
        service.bind_paris_borrower_adept(
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


def test_valid_explicit_grant_and_unbound_primary_keep_existing_semantics(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == ("bob", None)
    finally:
        service.close()
