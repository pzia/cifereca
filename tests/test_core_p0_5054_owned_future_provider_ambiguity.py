"""P0 #5054: a Library action cannot guess between multiple source origins."""
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from cifereca.core import selected_source_offer as selection


def _pos(provider, *, identity="alice", kind="owned", state="active"):
    return {
        "provider_id": provider, "identity_id": identity,
        "kind": kind, "state": state, "item_id": "edition-1",
        "account_id": "account-A",
        # Compatibility-only input: never used to authorize source selection.
        "entitlement_id": "obsolete-ownership",
    }


def _service():
    account = Account(identity_id="alice", provider_id="numilog",
                      credential_id="card-A", unique_id="account-A")
    return NS(current_identity=NS(identity_id="alice"), accounts=[account])


@pytest.mark.parametrize("other_provider", ["kobo", "future-library", "bnf-pret"])
def test_competing_origin_refuses_library_action_before_provider_call(
    monkeypatch, other_provider,
):
    monkeypatch.setattr(selection, "select_source_offer_target",
                        lambda *_a, **_kw: pytest.fail("ambiguous origin"))
    entry = {"possessions": [_pos("numilog"), _pos(other_provider)]}
    assert selection.select_library_source_offer_target(_service(), entry) is None


def test_single_source_uses_exact_account_not_historical_right(monkeypatch):
    marker = object()
    calls = []
    def select(_service, **coords):
        calls.append(coords)
        return marker
    monkeypatch.setattr(selection, "select_source_offer_target", select)
    entry = {"possessions": [
        _pos("numilog"), _pos("future-library", identity="bob"),
        _pos("future-library", state="expired"),
        _pos("future-library", kind="loan"),
    ]}
    assert selection.select_library_source_offer_target(_service(), entry) is marker
    assert calls == [{
        "provider_id": "numilog", "credential_id": "card-A",
        "item_id": "edition-1", "expected_identity_id": "alice",
    }]


def test_unregistered_provider_and_duplicate_accounts_never_become_a_target(monkeypatch):
    monkeypatch.setattr(selection, "select_source_offer_target",
                        lambda *_a, **_kw: pytest.fail("not exact"))
    service = _service()
    assert selection.select_library_source_offer_target(
        service, {"possessions": [_pos("future-library")]},
    ) is None
    service.accounts.append(Account(
        identity_id="alice", provider_id="numilog",
        credential_id="card-B", unique_id="account-A",
    ))
    assert selection.select_library_source_offer_target(
        service, {"possessions": [_pos("numilog")]},
    ) is None


@pytest.mark.parametrize("other_provider", [
    "future-library", "bnf-pret", "kobo", "", None,
])
def test_owned_download_target_never_guesses_among_multiple_origins(
    monkeypatch, other_provider,
):
    from cifereca.core import owned_library_download as action
    calls = []
    monkeypatch.setattr(
        selection, "select_library_source_offer_target",
        lambda *_args: calls.append("selected") or object(),
    )
    entry = {"possessions": [_pos("numilog"), _pos(other_provider)]}
    assert action.owned_library_download_target(_service(), entry) is None
    assert calls == []


def test_owned_download_target_allows_only_the_exact_single_origin(monkeypatch):
    from cifereca.core import owned_library_download as action
    target = object()
    calls = []
    monkeypatch.setattr(
        selection, "select_library_source_offer_target",
        lambda *_args: calls.append("selected") or target,
    )
    entry = {"possessions": [
        _pos("numilog"),
        _pos("future-library", identity="bob"),
        _pos("future-library", state="expired"),
        _pos("future-library", kind="loan"),
    ]}
    assert action.owned_library_download_target(_service(), entry) is target
    assert calls == ["selected"]
