"""P0 #5975: provider detail enrichment is headless and context-epoch fenced."""
from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import provider_explicit_enrichment as core
from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderCapabilityError
from cifereca.services import LibraryService
from tests.test_core_generic_explicit_enrichment_p0 import item


class Headless:
    _account_context_epoch = 17
    _source_context_epoch = 5

    def __init__(self):
        self.calls = []
        self.on_provider_call = None

    @property
    def current_identity(self):
        pytest.fail("Core provider detail queried mutable GTK navigation")

    def _session_for_source(self, provider, credential):
        self.calls.append(("lookup", provider, credential))

        def enrich(row):
            self.calls.append(("enrich", row.record_id))
            if self.on_provider_call is not None:
                self.on_provider_call()
            return replace(row, publisher="Public descriptive metadata")

        return NS(enrich_public_detail=enrich, enrich_owned_public_detail=enrich)


def _public():
    return item(source_collection="public_catalog", source_credential="__public__",
                owned=False, downloadable=False)


def _call(host, row=None, *, selected="alice", epoch=17):
    return core.enrich_provider_detail_once(
        host, row if row is not None else _public(),
        expected_identity_id="alice", selected_identity_id=selected,
        expected_context_epoch=epoch,
    )


def test_headless_worker_public_detail_never_reads_navigation():
    host = Headless()
    result = _call(host)
    assert result.publisher == "Public descriptive metadata"
    assert result.download_url is None and result.owned is False
    assert len(host.calls) == 2
    assert [name for name, *_ in host.calls] == ["lookup", "enrich"]


@pytest.mark.parametrize("selected,epoch", [(None, 17), ("bob", 17), ("alice", None),
                                               ("alice", 16), ("alice", -1)])
def test_missing_wrong_or_stale_identity_context_refuses_before_provider_get(selected, epoch):
    host = Headless()
    with pytest.raises(ProviderCapabilityError, match="explicit_public_enrichment_unavailable"):
        _call(host, selected=selected, epoch=epoch)
    assert host.calls == []


@pytest.mark.parametrize("mutate", ["account_epoch", "source_epoch", "metadata"])
def test_supplier_metadata_cannot_survive_context_or_source_rebind(mutate):
    host = Headless()
    def change():
        if mutate == "account_epoch":
            host._account_context_epoch += 2  # A -> B -> A
        elif mutate == "source_epoch":
            host._source_context_epoch += 1
    host.on_provider_call = change
    result = None
    if mutate == "metadata":
        def get_session(provider, credential):
            def compromise(row):
                host.calls.append(("enrich", row.record_id))
                return replace(row, download_url="https://fixture.invalid/private-token")
            host.calls.append(("lookup", provider, credential))
            return NS(enrich_public_detail=compromise)
        host._session_for_source = get_session
    with pytest.raises(ProviderCapabilityError, match="explicit_public_enrichment_unavailable"):
        result = _call(host)
    assert result is None and len(host.calls) == 2


def test_facade_pins_one_profile_and_blocks_aba_even_if_worker_returns(monkeypatch):
    class Facade:
        selected = NS(identity_id="alice")
        _account_context_epoch = 21
        reads = 0

        @property
        def current_identity(self):
            self.reads += 1
            return self.selected

    host = Facade()
    seen = []
    def safe_worker(_service, _row, **kwargs):
        seen.append(kwargs)
        return "public description"
    monkeypatch.setattr(core, "enrich_provider_detail_once", safe_worker)
    assert LibraryService.enrich_provider_search_result(
        host, object(), expected_identity_id="alice",
    ) == "public description"
    assert host.reads == 2
    assert seen == [{
        "expected_identity_id": "alice",
        "selected_identity_id": "alice",
        "expected_context_epoch": 21,
    }]

    def aba_worker(_service, _row, **kwargs):
        host._account_context_epoch += 2
        return "must be suppressed"
    monkeypatch.setattr(core, "enrich_provider_detail_once", aba_worker)
    with pytest.raises(ProviderCapabilityError, match="explicit_public_enrichment_unavailable"):
        LibraryService.enrich_provider_search_result(
            host, object(), expected_identity_id="alice",
        )


def test_core_worker_ast_contains_no_navigation_access():
    for fn in (core._scope, core.enrich_provider_detail_once):
        tree = ast.parse(inspect.getsource(fn))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "current_identity"
            for node in ast.walk(tree)
        )


class AdmittedHost:
    def __init__(self):
        self.identity = NS(identity_id="alice")
        self._source_context_epoch = 5
        self._account_context_epoch = 17
        self.storage = NS()
        self.session = NS(provider_id="synthetic-public", credential_id="__public__",
                          connected=True, enrich_public_detail=self.enrich)
        self._sessions = {("synthetic-public", "__public__"): self.session}
        self.calls = []
        self.on_call = None

    @property
    def current_identity(self):
        pytest.fail("admitted enrichment read the selector")

    def identity_by_id(self, identity_id):
        return self.identity if identity_id == "alice" else None

    def _session_key(self, provider, credential):
        return provider, credential

    def _provider_enabled_for_identity(self, provider_id, identity_id):
        assert identity_id == "alice"
        return True

    def enrich(self, row):
        self.calls.append(row.record_id)
        if self.on_call:
            self.on_call()
        return replace(row, publisher="Public descriptive metadata")


def _admitted_row(**changes):
    return replace(_public(), source_provider="synthetic-public", **changes)


@pytest.mark.parametrize("change", ["navigation", "during_navigation", "source",
                                     "session", "identity", "during_revoke"])
def test_admitted_enrichment_has_exact_lifetime_not_navigation(change):
    host = AdmittedHost()
    row = _admitted_row()
    work = LibraryService.admit_provider_search_enrichment(
        host, row, identity_id="alice", user_confirmed=True,
    )
    assert host.calls == []
    if change == "navigation":
        host._account_context_epoch += 2
    elif change == "during_navigation":
        host.on_call = lambda: setattr(host, "_account_context_epoch", 19)
    elif change == "source":
        host._source_context_epoch += 1
    elif change == "session":
        host._sessions[("synthetic-public", "__public__")] = NS(
            provider_id="synthetic-public", credential_id="__public__",
            connected=True, enrich_public_detail=host.enrich,
        )
    elif change == "identity":
        host.identity = NS(identity_id="alice")
    elif change == "during_revoke":
        host.on_call = lambda: setattr(host, "_source_context_epoch", 6)
    if change in ("navigation", "during_navigation"):
        assert work().publisher == "Public descriptive metadata"
    else:
        with pytest.raises(ProviderCapabilityError):
            work()
    assert len(host.calls) == (1 if change in (
        "navigation", "during_navigation", "during_revoke") else 0)
    with pytest.raises(ProviderCapabilityError):
        work()


@pytest.mark.parametrize("change", ["ok", "detach", "during_detach",
                                     "foreign_account", "unconfirmed"])
def test_admitted_private_enrichment_rechecks_persisted_borrower(change):
    host = AdmittedHost()
    account = NS(identity_id="alice", provider_id="synthetic-private",
                 credential_id="alice-cred", unique_id="borrower")
    host.accounts = [account]
    host.accounts_for_identity = lambda identity_id: host.accounts if identity_id == "alice" else []
    host.session.provider_id = account.provider_id
    host.session.credential_id = account.credential_id
    host.session.enrich_owned_public_detail = host.enrich
    host._sessions = {(account.provider_id, account.credential_id): host.session}
    binding = [True]
    host.storage.identity_for_connection = lambda *_: "alice"
    host.storage.provider_connections = lambda **_: [
        {"provider_id": account.provider_id, "credential_id": account.credential_id,
         "enabled": True}
    ]
    host.storage.provider_scope_account_bound = lambda **_: binding[0]
    row = item(source_provider=account.provider_id, source_credential=account.credential_id,
               source_account_id="foreign" if change == "foreign_account" else "borrower")
    if change in ("foreign_account", "unconfirmed"):
        with pytest.raises(ProviderCapabilityError):
            LibraryService.admit_provider_search_enrichment(
                host, row, identity_id="alice", user_confirmed=change != "unconfirmed")
        assert host.calls == []
        return
    work = LibraryService.admit_provider_search_enrichment(
        host, row, identity_id="alice", user_confirmed=True)
    assert host.calls == []
    if change == "detach":
        binding[0] = False
    elif change == "during_detach":
        host.on_call = lambda: binding.__setitem__(0, False)
    if change == "ok":
        assert work().publisher == "Public descriptive metadata"
    else:
        with pytest.raises(ProviderCapabilityError):
            work()
    assert len(host.calls) == (0 if change == "detach" else 1)
