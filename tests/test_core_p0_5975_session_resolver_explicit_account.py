"""#5975 / #5851: session resolution must never infer GTK-selected account."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core import provider_sessions
from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def account(owner, code):
    return Account(identity_id=owner, provider_id="synthetic",
                   credential_id=code, unique_id=f"borrower-{owner}")


@pytest.mark.parametrize("invalid", [None, False, 0, {}, [], object(),
                                     NS(identity_id="alice", provider_id="synthetic", credential_id="a"),
                                     Account(identity_id="", provider_id="synthetic", credential_id="a"),
                                     Account(identity_id="alice", provider_id="", credential_id="a"),
                                     Account(identity_id="alice", provider_id="synthetic", credential_id=None)])
def test_worker_rejects_unproven_account_before_any_session_access(invalid):
    def forbidden(*_args):
        pytest.fail("unproven borrower reached session resolver")
    host = NS(current_account=account("alice", "a"), _session_key=forbidden,
              _sessions=NS(get=forbidden))
    with pytest.raises(ProviderConfigurationError, match="account_session_explicit_owner_required"):
        provider_sessions._session_for_account(host, invalid)


def test_worker_ignores_navigation_after_explicit_account():
    alice = account("alice", "a")
    bob = account("bob", "b")
    session_a, session_b = object(), object()
    host = NS(current_account=bob,
              _session_key=lambda p, c: (p, c),
              _sessions={("synthetic", "a"): session_a, ("synthetic", "b"): session_b})
    assert provider_sessions._session_for_account(host, alice) is session_a
    host.current_account = alice
    assert provider_sessions._session_for_account(host, bob) is session_b


def test_public_facade_pins_current_account_at_sync_boundary(monkeypatch):
    alice, bob = account("alice", "a"), account("bob", "b")
    host = LibraryService.__new__(LibraryService)
    host.current_account = alice
    admitted = []

    def worker(service, account):
        admitted.append(account)
        host.current_account = bob
        return "alice-session"

    monkeypatch.setattr(provider_sessions, "_session_for_account", worker)
    assert host._session_for_account() == "alice-session"
    assert admitted == [alice]
    assert host.current_account is bob


def test_legacy_pnb_preferences_refuses_before_selected_account_is_used():
    alice, bob = account("alice", "a"), account("bob", "b")
    host = LibraryService.__new__(LibraryService)
    host.current_account = alice
    calls = []

    def session_for_account(admitted):
        calls.append(admitted)
        host.current_account = bob
        return NS(digital_preferences=lambda applied: {"borrower": applied.identity_id})

    host._session_for_account = session_for_account
    with pytest.raises(ProviderConfigurationError, match="pnb_preferences_explicit_account_required"):
        host.pnb_preferences()
    assert calls == []
    assert host.current_account is alice


def test_legacy_pnb_preferences_does_not_read_gtk_selection():
    class Headless:
        @property
        def current_account(self):
            pytest.fail("retired GET inspected mutable GTK selection")

        def _session_for_account(self, _account):
            pytest.fail("retired GET called provider session")

    with pytest.raises(ProviderConfigurationError, match="pnb_preferences_explicit_account_required"):
        LibraryService.pnb_preferences(Headless())


@pytest.mark.parametrize("selected", [
    None, "", 0, {}, [], object(),
    NS(identity_id="alice", provider_id="synthetic", credential_id="a"),
    Account(identity_id="", provider_id="synthetic", credential_id="a"),
    Account(identity_id="alice", provider_id="", credential_id="a"),
    Account(identity_id="alice", provider_id="synthetic", credential_id=None),
])
def test_legacy_pnb_preferences_never_infers_any_selected_borrower(selected):
    class Host:
        current_account = selected

        def _session_for_account(self, _account):
            pytest.fail("legacy PNB preferences requested a provider GET")

    with pytest.raises(ProviderConfigurationError, match="pnb_preferences_explicit_account_required"):
        LibraryService.pnb_preferences(Host())
