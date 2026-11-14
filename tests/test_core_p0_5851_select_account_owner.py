"""#5975 / #5851: account selection cannot retain a stale GTK owner."""
from __future__ import annotations

from threading import Lock
from types import SimpleNamespace as NS

import pytest

from cifereca.identities import Identity
from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def _service(candidate_identity):
    host = LibraryService.__new__(LibraryService)
    first = Account(unique_id="a", provider_id="synthetic",
                    credential_id="cred-a", identity_id="alice")
    second = Account(unique_id="b", provider_id="synthetic",
                     credential_id="cred-b", identity_id=candidate_identity)
    host.accounts = [first, second]
    host.identities = [Identity("alice", "Alice"), Identity("bob", "Bob")]
    host.current_identity = host.identities[0]
    host.current_account = first
    host.last_loans = ["Alice private loan"]
    host._account_context_epoch = 12
    host._search_generation_lock = Lock()
    host._sessions = {("synthetic", "cred-b"): NS()}
    host._session_key = lambda provider, credential: (provider, credential)
    host._active_session_key = ("synthetic", "cred-a")
    return host, first, second


@pytest.mark.parametrize("identity", ["deleted", "", None, False, 0, [], {}])
def test_invalid_borrower_identity_does_not_mutate_navigation_or_loans(identity):
    host, first, second = _service(identity)
    with pytest.raises(ProviderConfigurationError, match="account_selection_identity_unproven"):
        host.select_account("b", "synthetic", "cred-b")
    assert host.current_account is first
    assert host.current_identity.identity_id == "alice"
    assert host._account_context_epoch == 12
    assert host._active_session_key == ("synthetic", "cred-a")
    assert host.last_loans == ["Alice private loan"]


def test_valid_cross_identity_selection_updates_owner_and_invalidates_loans():
    host, first, second = _service("bob")
    assert host.select_account("b", "synthetic", "cred-b") is second
    assert host.current_account is second
    assert host.current_identity is host.identities[1]
    assert host._account_context_epoch == 13
    assert host._active_session_key == ("synthetic", "cred-b")
    assert host.last_loans == []
