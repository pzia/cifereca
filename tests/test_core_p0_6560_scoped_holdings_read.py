"""Direct Core holdings calls have their own reader and source authority.

Synthetic sessions, deterministic barriers; no GTK, real provider or network.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from threading import Barrier
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.search_orchestration import capture_search_read_scope
from cifereca.models import ItemDetail, SearchResult
from cifereca.plugin_api import Cancelled
from cifereca.services import LibraryService


def row(credential="card-a", account="account-a"):
    return SearchResult(
        id="same", record_id="same", docbase="CATALOG", medium="physical",
        title="Synthetic book", author=None, type=None, format=None,
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="CATALOG",
        source_provider="synthetic", source_credential=credential,
        source_collection="catalog", source_account_id=account,
    )


def detail(item, label):
    return ItemDetail(
        record_id=item.record_id, docbase=item.docbase, title=item.title,
        author=None, type=None, year=None, thumbnail_url=None,
        availability_status="available", availability_label=label,
        holdings=[], source_provider=item.source_provider,
        source_credential=item.source_credential,
    )


class Storage:
    def __init__(self):
        self.owners = {"card-a": "alice", "card-b": "bob"}
        self.cache = {}
        self.reads = []

    def identity_for_connection(self, provider, credential):
        assert provider == "synthetic"
        return self.owners.get(credential)

    def cache_get(self, namespace, key):
        self.reads.append((namespace, key))
        return self.cache.get((namespace, key))

    def cache_put(self, namespace, key, value, ttl):
        self.cache[namespace, key] = value


class Core:
    holdings_for_scope = LibraryService.holdings_for_scope

    def __init__(self):
        self.identities = {key: NS(identity_id=key) for key in ("alice", "bob")}
        self.storage = Storage()
        self.config = NS(get=lambda *args: 300)
        self._source_context_epoch = 7
        self._sessions = {}
        self.disabled = set()
        self.policy_reads = []

    @property
    def current_identity(self):
        pytest.fail("scoped read consulted mutable navigation")

    @property
    def current_account(self):
        pytest.fail("scoped read consulted mutable navigation")

    @property
    def _account_context_epoch(self):
        pytest.fail("scoped read consulted navigation epoch")

    def identity_by_id(self, key):
        return self.identities.get(key)

    def _provider_enabled_for_identity(self, provider, identity_id):
        assert provider == "synthetic"
        self.policy_reads.append(identity_id)
        return identity_id not in self.disabled

    @staticmethod
    def _session_key(provider, credential):
        return provider, credential

    def install(self, credential, handler):
        self._sessions["synthetic", credential] = NS(holdings=handler)

    def read(self, scope, item, *, force=False):
        return LibraryService.holdings_for_scope(self, scope, item, force=force)


def test_two_clients_same_edition_concurrent_and_cache_separated():
    core = Core()
    a, b = (capture_search_read_scope(core, key) for key in ("alice", "bob"))
    gate = Barrier(2)
    calls = []

    def handle(item, _terms):
        calls.append(item.source_credential)
        gate.wait(timeout=5)
        return detail(item, item.source_credential)

    core.install("card-a", handle)
    core.install("card-b", handle)
    rows = row(), row("card-b", "account-b")
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(core.read, a, rows[0], force=True)
        two = pool.submit(core.read, b, rows[1], force=True)
        assert one.result(timeout=10).availability_label == "card-a"
        assert two.result(timeout=10).availability_label == "card-b"

    assert sorted(calls) == ["card-a", "card-b"]
    assert len(core.storage.cache) == 2
    keys = [json.loads(key) for namespace, key in core.storage.cache]
    assert {(key[0], key[1], key[-1]) for key in keys} == {
        ("v3", "alice", "account-a"), ("v3", "bob", "account-b"),
    }
    assert core.read(a, rows[0]).availability_label == "card-a"
    assert core.read(b, rows[1]).availability_label == "card-b"
    assert len(calls) == 2
    assert set(core.policy_reads) == {"alice", "bob"}


@pytest.mark.parametrize("revoke", ["epoch", "identity", "owner", "policy"])
def test_real_revocation_during_provider_read_prevents_cache_and_result(revoke):
    core = Core()
    scope = capture_search_read_scope(core, "alice")

    def handle(item, _terms):
        if revoke == "epoch":
            core._source_context_epoch += 1
        elif revoke == "identity":
            core.identities["alice"] = NS(identity_id="alice")
        elif revoke == "owner":
            core.storage.owners["card-a"] = "bob"
        else:
            core.disabled.add("alice")
        return detail(item, "private")

    core.install("card-a", handle)
    observed = core.read(scope, row(), force=True)
    assert observed.availability_status == "unknown"
    assert observed.availability_label != "private"
    assert core.storage.cache == {}


def test_missing_foreign_and_revoked_context_never_read_cache_or_provider():
    core = Core()
    scope = capture_search_read_scope(core, "alice")
    core.install("card-b", lambda *_: pytest.fail("foreign GET"))
    assert core.read(scope, row("card-b"), force=False).availability_status == "unknown"
    assert core.storage.reads == []
    core._source_context_epoch += 1
    with pytest.raises(Cancelled, match="holdings_read_scope_revoked"):
        core.read(scope, row())
    with pytest.raises(Cancelled, match="holdings_read_scope_revoked"):
        core.read(None, row())
    assert core.storage.reads == []


def test_cache_hit_revoked_and_legacy_cache_not_inherited():
    core = Core()
    scope = capture_search_read_scope(core, "alice")
    item = row()
    core.install("card-a", lambda item, _: detail(item, "fresh"))
    legacy_key = json.dumps(("v2", "alice", "synthetic", "card-a",
                             "CATALOG", "CATALOG", "catalog", "same"),
                            separators=(",", ":"))
    core.storage.cache["holdings", legacy_key] = asdict(detail(item, "old"))
    assert core.read(scope, item).availability_label == "fresh"
    original = core.storage.cache_get

    def revoke(namespace, key):
        result = original(namespace, key)
        core._source_context_epoch += 1
        return result

    core.storage.cache_get = revoke
    assert core.read(scope, item).availability_status == "unknown"


def test_public_label_does_not_authorize_foreign_private_session():
    core = Core()
    scope = capture_search_read_scope(core, "alice")
    item = row("card-b")
    item.source_collection = "public"
    item.owned = False
    core.install("card-b", lambda *_: pytest.fail("foreign private GET"))
    assert core.read(scope, item).availability_status == "unknown"
    assert core.storage.reads == []


def test_catalog_broker_facets_use_admitted_reader_without_navigation():
    core = Core()
    broker = CatalogBrokerImpl(core)
    alice = broker.for_identity("alice")
    bob = broker.for_identity("bob")
    core.install("card-a", lambda item, _: detail(item, "alice"))
    core.install("card-b", lambda item, _: detail(item, "bob"))
    assert alice.holdings(row(), force=True).availability_label == "alice"
    assert bob.holdings(row("card-b", "account-b"), force=True).availability_label == "bob"
    assert bob.holdings(row()).availability_status == "unknown"
    with pytest.raises(ValueError):
        broker.for_identity("missing")
    core._source_context_epoch += 1
    with pytest.raises(Cancelled):
        alice.holdings(row())
