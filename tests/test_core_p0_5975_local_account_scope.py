"""P0 #5975 / #5851: local bootstrap never fabricates a default borrower.

No provider, network, GTK or persistent storage is involved in this test.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.identities import Identity
from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def _service(selected, configured):
    service = LibraryService.__new__(LibraryService)
    service.current_identity = selected
    service.identities = list(configured)
    return service


def test_local_account_stays_bound_to_exact_configured_profile_on_switch():
    alice, bob = Identity("alice", "Reader A"), Identity("bob", "Reader B")
    service = _service(alice, [alice, bob])
    account_a = service._local_account()
    assert type(account_a) is Account
    assert (account_a.identity_id, account_a.provider_id,
            account_a.credential_id, account_a.unique_id) == (
                "alice", "local", "local", "local",
            )
    service.current_identity = bob
    account_b = service._local_account()
    assert account_b.identity_id == "bob"
    assert account_a.identity_id == "alice"


@pytest.mark.parametrize("selected", [
    None, False, 0, {}, SimpleNamespace(identity_id="alice"),
    Identity("", "Missing"), Identity(" default ", "Whitespace"),
    Identity("alice\x00other", "Control"),
    Identity("x" * 1025, "Oversized"),
])
def test_missing_or_malformed_selection_never_becomes_default(selected):
    alice = Identity("alice", "Reader A")
    # A malformed native Identity must be refused even if configured as-is;
    # do not let a separate owner mismatch mask malformed-id acceptance.
    configured = [selected] if type(selected) is Identity else [alice]
    service = _service(selected, configured)
    with pytest.raises(ProviderConfigurationError, match="^local_account_identity_required$"):
        service._local_account()


def test_selected_identity_must_be_the_configured_object_not_matching_label():
    configured = Identity("alice", "Reader A")
    stale_copy = Identity("alice", "Another instance")
    service = _service(stale_copy, [configured])
    with pytest.raises(ProviderConfigurationError, match="local_account_identity_required"):
        service._local_account()


def test_identity_removal_or_duplicate_reference_revokes_local_account():
    alice = Identity("alice", "Reader A")
    service = _service(alice, [alice])
    assert service._local_account().identity_id == "alice"
    service.identities.clear()
    with pytest.raises(ProviderConfigurationError, match="local_account_identity_required"):
        service._local_account()
    service.identities.extend([alice, alice])
    with pytest.raises(ProviderConfigurationError, match="local_account_identity_required"):
        service._local_account()
    service.identities[:] = [alice, Identity("alice", "Duplicate id")]
    with pytest.raises(ProviderConfigurationError, match="local_account_identity_required"):
        service._local_account()


def test_invalid_selected_identity_does_not_construct_account(monkeypatch):
    from cifereca import services

    service = _service(None, [Identity("default", "Legacy")])

    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid local source must not construct an Account")

    monkeypatch.setattr(services, "Account", forbidden)
    with pytest.raises(ProviderConfigurationError, match="local_account_identity_required"):
        service._local_account()
