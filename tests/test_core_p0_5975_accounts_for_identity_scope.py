"""P0 #5975 / #5851: an unscoped account enumeration is never all profiles."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from cifereca.services import LibraryService


def _service():
    service = LibraryService.__new__(LibraryService)
    service.accounts = [
        Account(identity_id="alice", provider_id="synthetic", credential_id="a",
                unique_id="shared"),
        Account(identity_id="bob", provider_id="synthetic", credential_id="b",
                unique_id="shared"),
    ]
    service.current_identity = NS(identity_id="alice")
    return service


@pytest.mark.parametrize("scope", [None, "", " ", "\t", False, 0, [], {}])
def test_account_enumeration_never_infers_gtk_or_unions_without_scope(scope):
    service = _service()
    assert service.accounts_for_identity(scope) == []
    service.current_identity = None
    assert service.accounts_for_identity(scope) == []


def test_explicit_identity_account_enumeration_survives_navigation_and_repeated_uid():
    service = _service()
    alice, bob = service.accounts
    assert service.accounts_for_identity("alice") == [alice]
    service.current_identity = NS(identity_id="bob")
    assert service.accounts_for_identity("alice") == [alice]
    assert service.accounts_for_identity("bob") == [bob]
    assert service.accounts_for_identity("unknown") == []


def test_account_enumeration_is_independent_of_storage_or_provider(monkeypatch):
    service = _service()
    service.storage = NS(
        provider_accounts=lambda: pytest.fail("account enumeration invoked storage")
    )
    service._sessions = NS(
        get=lambda *_a: pytest.fail("account enumeration invoked session")
    )
    assert service.accounts_for_identity() == []
    assert [a.identity_id for a in service.accounts_for_identity("alice")] == ["alice"]
