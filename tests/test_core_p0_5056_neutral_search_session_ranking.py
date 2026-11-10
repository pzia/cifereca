"""#5056: catalogue source ranking has no supplier-specific branch or permission."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.search_session_selection import choose_search_sessions


class Storage:
    def __init__(self, owners):
        self.owners = owners
        self.calls = []

    def identity_for_connection(self, provider, credential):
        self.calls.append((provider, credential))
        return self.owners.get((provider, credential))


def session(provider, credential):
    return NS(provider_id=provider, credential_id=credential)


@pytest.mark.parametrize("provider", ["fixture-a", "fixture-b"])
def test_preferred_account_is_only_honored_with_same_exact_owner(provider):
    storage = Storage({
        (provider, "foreign"): "bob",
        (provider, "local"): "alice",
        (provider, "preferred"): "alice",
    })
    service = NS(storage=storage)
    ordered = [
        session(provider, "foreign"),
        session(provider, "local"),
        session(provider, "preferred"),
        session(provider, "foreign"),
    ]
    result = choose_search_sessions(
        service, ordered, NS(identity_id="alice"),
        preferred_credentials={provider: "preferred"},
    )
    assert result == [ordered[2]]
    assert set(storage.calls) == {
        (provider, "foreign"), (provider, "local"),
        (provider, "preferred"),
    }


def test_foreign_preferred_never_beats_active_owner():
    storage = Storage({
        ("fixture-a", "foreign"): "bob",
        ("fixture-a", "local"): "alice",
    })
    rows = [session("fixture-a", "foreign"), session("fixture-a", "local")]
    result = choose_search_sessions(
        NS(storage=storage), rows, NS(identity_id="alice"),
        preferred_credentials={"fixture-a": "foreign"},
    )
    assert result == [rows[1]]


def test_multiple_providers_are_selected_independently_and_stably():
    rows = [
        session("fixture-a", "a-foreign"),
        session("fixture-b", "b-local"),
        session("fixture-a", "a-local"),
        session("fixture-b", "b-foreign"),
    ]
    service = NS(storage=Storage({
        ("fixture-a", "a-local"): "alice",
        ("fixture-b", "b-local"): "alice",
    }))
    assert choose_search_sessions(
        service, rows, NS(identity_id="alice"),
    ) == [rows[2], rows[1]]


def test_unknown_owner_cannot_be_promoted_by_preference():
    rows = [session("fixture-a", "unknown"), session("fixture-a", "local")]
    service = NS(storage=Storage({("fixture-a", "local"): "alice"}))
    assert choose_search_sessions(
        service, rows, NS(identity_id="alice"),
        preferred_credentials={"fixture-a": "unknown"},
    ) == [rows[1]]
