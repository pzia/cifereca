"""#5975 / #5851: the frozen active_session property cannot select GTK state.

The retired accessor is an inert compatibility seam. Exact account resolution
remains available separately and does not consult mutable UI selection.
"""
from __future__ import annotations

import inspect

import pytest

from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def _account(identity, credential):
    return Account(identity_id=identity, provider_id="synthetic",
                   credential_id=credential, unique_id="borrower")


def test_active_session_is_inert_even_when_navigation_selects_connected_source():
    alice, bob = _account("alice", "credential-a"), _account("bob", "credential-b")
    service = LibraryService.__new__(LibraryService)
    session_a, session_b = object(), object()
    service._sessions = {
        service._session_key("synthetic", "credential-a"): session_a,
        service._session_key("synthetic", "credential-b"): session_b,
    }
    service._active_session_key = service._session_key("synthetic", "credential-a")
    service.current_account = alice
    with pytest.raises(ProviderConfigurationError,
                       match="^active_session_explicit_source_required$"):
        _ = service.active_session
    service._active_session_key = service._session_key("synthetic", "credential-b")
    service.current_account = bob
    with pytest.raises(ProviderConfigurationError,
                       match="^active_session_explicit_source_required$"):
        _ = service.active_session
    # Typed, exact borrowers retain the explicit source resolver; no default
    # or active-session alias can silently select another identity.
    assert service._session_for_account(alice) is session_a
    assert service._session_for_account(bob) is session_b


def test_retired_accessor_does_not_read_session_registry_or_selection():
    class Poison:
        @property
        def _active_session_key(self):
            pytest.fail("accessed active source selection")

        @property
        def _sessions(self):
            pytest.fail("accessed connected session registry")

        @property
        def current_account(self):
            pytest.fail("accessed selected borrower")

        @property
        def current_identity(self):
            pytest.fail("accessed GTK identity")

    with pytest.raises(ProviderConfigurationError,
                       match="^active_session_explicit_source_required$"):
        LibraryService.active_session.fget(Poison())


def test_active_session_public_abi_remains_a_property_without_arguments():
    descriptor = inspect.getattr_static(LibraryService, "active_session")
    assert type(descriptor) is property
    assert str(inspect.signature(descriptor.fget)) == (
        "(self) -> 'ProviderSession'"
    )
