"""P0 #5851: grant writer must not coerce untrusted ADEPT provenance."""
from dataclasses import replace

import pytest

from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


class Hostile:
    def __str__(self):
        raise AssertionError("private provider data must not be coerced")

    def __bool__(self):
        raise AssertionError("private provider data must not be truth-tested")


class Subclass(str):
    def strip(self, *args):
        raise AssertionError("untrusted str subclass must not be normalized")


@pytest.mark.parametrize("field,bad", [
    ("credential_id", Hostile()),
    ("credential_id", Subclass("alice-paris")),
    ("credential_id", None),
    ("account_id", Hostile()),
    ("account_id", Subclass("linked-123")),
    ("account_id", None),
    ("borrower_identity_id", Hostile()),
    ("borrower_identity_id", Subclass("bob")),
    ("borrower_identity_id", None),
    ("confirmed", Hostile()),
    ("confirmed", 1),
    ("confirmed", "true"),
])
def test_grant_writer_rejects_untrusted_coordinates_without_sqlite_write(
    tmp_path, monkeypatch, field, bad,
):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        calls = []
        def forbid(**kwargs):
            calls.append(kwargs)
            raise AssertionError("malformed grant must never reach storage")
        monkeypatch.setattr(service.storage, "paris_borrower_adept_binding_put", forbid)
        arguments = dict(credential_id="alice-paris", account_id="linked-123",
                         borrower_identity_id="bob", confirmed=True)
        arguments[field] = bad
        with pytest.raises(ValueError):
            service.bind_paris_borrower_adept(**arguments)
        assert calls == []
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None
    finally:
        service.close()


@pytest.mark.parametrize("field,bad", [
    ("provider_id", Hostile()),
    ("provider_id", Subclass("paris")),
    ("credential_id", Hostile()),
    ("credential_id", Subclass("alice-paris")),
    ("unique_id", Hostile()),
    ("unique_id", Subclass("linked-123")),
    ("identity_id", Hostile()),
    ("identity_id", Subclass("alice")),
    ("is_primary", Hostile()),
])
def test_grant_writer_requires_native_plugin_account_evidence(
    tmp_path, monkeypatch, field, bad,
):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.accounts[1] = replace(service.accounts[1], **{field: bad})
        monkeypatch.setattr(
            service.storage, "paris_borrower_adept_binding_put",
            lambda **kwargs: pytest.fail("invalid source account must not write grant"),
        )
        with pytest.raises(ValueError):
            service.bind_paris_borrower_adept(
                credential_id="alice-paris", account_id="linked-123",
                borrower_identity_id="bob", confirmed=True,
            )
    finally:
        service.close()


def test_valid_explicit_writer_still_pins_exact_borrower_and_profile(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        grant = service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert grant["borrower_identity_id"] == "bob"
        assert grant["profile_id"] == "bob-profile"
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == ("bob", None)
    finally:
        service.close()
