"""P0 #5975: Library offer selection is headless and epoch scoped.

No selected source is permission to GET: these tests only build passive
targets, inspect exact binding, and poison every navigation access in Core.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from cifereca.core import selected_source_offer as selection
from cifereca.core import owned_library_download as owned
from cifereca.core import selected_owned_search as search
from cifereca.services import LibraryService


class SourceSession:
    selected_purchase_branch = "private-books"

    def downloadable_selected_delivery_candidate_guarded(self, **_kwargs):
        pytest.fail("passive selection must never GET a provider")


class Headless:
    def __init__(self):
        self.account = Account(
            identity_id="alice", provider_id="future-books",
            credential_id="card-A", unique_id="account-A",
        )
        self.session = SourceSession()
        self.accounts = [self.account]
        self._sessions = {("future-books", "card-A"): self.session}
        self._session_key = lambda *scope: scope
        self._account_context_epoch = 7

    @property
    def current_identity(self):
        pytest.fail("a headless source worker consulted GTK navigation")


def test_headless_selector_binds_unknown_provider_to_explicit_owner(monkeypatch):
    host = Headless()
    seen = []
    monkeypatch.setattr(selection, "has_complete_delivery_abi", lambda _session: True)
    monkeypatch.setattr(
        selection, "exact_private_inventory_binding_valid",
        lambda _service, account, *, session, provider_id: (
            seen.append((account, session, provider_id)) or True
        ),
    )
    target = selection.select_source_offer_target(
        host, provider_id="future-books", credential_id="card-A",
        item_id="edition-1", identity_id="alice",
    )
    assert type(target) is selection.SourceOfferTarget
    assert target.identity_id == "alice"
    assert target.selection_epoch == 7
    assert seen == [(host.account, host.session, "future-books")]
    assert selection._resolve(host, target) == (target, host.account)

    # The same active identity can reappear after A→B→A. The epoch never does.
    host._account_context_epoch = 9
    with pytest.raises(ValueError, match="selected_source_identity_changed"):
        selection._resolve(host, target)
    assert selection.select_source_offer_target(
        host, provider_id="future-books", credential_id="card-A",
        item_id="edition-1", identity_id="bob",
    ) is None


def test_missing_identity_fails_before_a_source_or_navigation_lookup():
    class Poison:
        def __getattr__(self, key):
            pytest.fail(f"unexpected Core lookup {key}")

    host = Poison()
    assert selection.select_source_offer_target(
        host, provider_id="future-books", credential_id="card-A",
        item_id="edition-1",
    ) is None
    assert selection.select_library_source_offer_target(
        host, {"possessions": []},
    ) is None
    assert owned.owned_library_download_target(
        host, {"possessions": []},
    ) is None


def test_library_card_uses_one_identity_for_all_origin_preflights(monkeypatch):
    host = Headless()
    accounts = [
        {"identity_id": "alice", "provider_id": "future-books",
         "account_id": "account-A", "item_id": "edition-1",
         "kind": "owned", "state": "active"},
        {"identity_id": "bob", "provider_id": "future-books",
         "account_id": "other", "item_id": "edition-2",
         "kind": "owned", "state": "active"},
    ]
    marker = object()
    calls = []
    monkeypatch.setattr(
        selection, "select_source_offer_target",
        lambda _service, **scope: calls.append(scope) or marker,
    )
    assert owned.owned_library_download_target(
        host, {"possessions": accounts}, identity_id="alice",
    ) is marker
    assert calls == [{
        "provider_id": "future-books", "credential_id": "card-A",
        "item_id": "edition-1", "identity_id": "alice",
    }]
    calls.clear()
    assert owned.owned_library_download_target(
        host, {"possessions": [accounts[0], dict(accounts[0])]},
        identity_id="alice",
    ) is None
    assert calls == []


def test_facade_blocks_a_b_a_selection_even_if_the_object_is_restored(monkeypatch):
    class Facade:
        current = NS(identity_id="alice")
        _account_context_epoch = 7

        @property
        def current_identity(self):
            return self.current

    host = Facade()
    captured = []
    from cifereca.core import owned_library_download

    def selected(_service, _entry, *, identity_id):
        captured.append(identity_id)
        host._account_context_epoch += 2
        return object()

    monkeypatch.setattr(owned_library_download, "owned_library_download_target", selected)
    assert LibraryService.owned_library_download_target(host, {}) is None
    assert captured == ["alice"]

    def steady(_service, _entry, *, identity_id):
        captured.append(identity_id)
        return "target"

    monkeypatch.setattr(owned_library_download, "owned_library_download_target", steady)
    assert LibraryService.owned_library_download_target(host, {}) == "target"
    assert captured == ["alice", "alice"]


def test_core_selection_workers_do_not_reference_current_identity():
    for module, names in (
        (selection, (
            "_resolve", "select_source_offer_target",
            "select_library_source_offer_target",
        )),
        (owned, ("owned_library_download_target",)),
        (search, ("obtain_selected_owned",)),
    ):
        for name in names:
            code = inspect.getsource(getattr(module, name))
            tree = ast.parse(code)
            assert not any(
                isinstance(node, ast.Attribute) and node.attr == "current_identity"
                for node in ast.walk(tree)
            )
            assert "current_identity" not in code
