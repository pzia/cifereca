"""P0 #5851: B contract admits only positively attested native v16 grants."""
from dataclasses import replace

import pytest

from tests.test_paris_borrower_adept_binding import service_with_linked_borrower
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account


COORDS = dict(provider_id="paris", credential_id="alice-paris",
              account_id="linked-123", connection_identity_id="alice")


def _explicit(service):
    _persist_account(service.storage)
    service.storage.adept_borrower_grant_put(
        provider_id="paris", credential_id="alice-paris",
        account_id="linked-123", connection_identity_id="alice",
        borrower_identity_id="bob", profile_id="bob-profile",
        confirmed=True,
    )


def test_explicit_grant_is_positive_not_legacy_or_ui_inference(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        check = service.explicit_adept_borrower_grant_for_source
        assert check(**COORDS) == (None, "adept_borrower_binding_required")
        service.current_identity = service.identity_by_id("bob")
        assert check(**COORDS) == (None, "adept_borrower_binding_required")
        _explicit(service)
        assert check(**COORDS) == ("bob", None)
        service.current_identity = service.identity_by_id("alice")
        assert check(**COORDS) == ("bob", None)
    finally:
        service.close()


def test_foreign_provider_cannot_reuse_owning_credential_or_account(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _explicit(service)
        check = service.explicit_adept_borrower_grant_for_source
        assert check(**COORDS) == ("bob", None)
        assert check(**{**COORDS, "provider_id": "kobo"}) == (
            None, "adept_borrower_ambiguous",
        )
        assert check(**{**COORDS, "credential_id": "other"}) == (
            None, "adept_borrower_ambiguous",
        )
        assert check(**{**COORDS, "account_id": "other"}) == (
            None, "adept_borrower_ambiguous",
        )
        assert check(**{**COORDS, "connection_identity_id": "bob"}) == (
            None, "adept_borrower_ambiguous",
        )
    finally:
        service.close()


def test_profile_reconfirmation_required_even_when_rotated_back(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        _explicit(service)
        check = service.explicit_adept_borrower_grant_for_source
        assert check(**COORDS) == ("bob", None)
        service.storage.adept_profile_binding_set("identity", "bob", "new-profile")
        service.storage.adept_profile_binding_set("identity", "bob", "bob-profile")
        assert check(**COORDS) == (None, "adept_borrower_binding_required")
    finally:
        service.close()


class _Hostile:
    def __str__(self):
        raise AssertionError("private source must never be converted")
    def __bool__(self):
        raise AssertionError("private source must never be tested")


@pytest.mark.parametrize("field,bad", [
    ("provider_id", _Hostile()),
    ("provider_id", None),
    ("credential_id", _Hostile()),
    ("account_id", _Hostile()),
    ("connection_identity_id", _Hostile()),
])
def test_untyped_source_cannot_query_grants(tmp_path, monkeypatch, field, bad):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        def forbidden(**kwargs):
            pytest.fail("unproven source must not reach grant storage")
        monkeypatch.setattr(service.storage, "adept_borrower_grant_get", forbidden)
        assert service.explicit_adept_borrower_grant_for_source(
            **{**COORDS, field: bad},
        ) == (None, "adept_borrower_ambiguous")
    finally:
        service.close()
