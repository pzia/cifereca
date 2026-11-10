"""P0 #5056: catalogue-owned flags never create rights for any provider.

Integration against the real remote-mirror projection and the already
reviewed Core purchase witness (#5073). All providers are synthetic.
No network, account activation or new personal ledger writes.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.search_orchestration import _mirror_remote_results
from cifereca.models import SearchResult


class Storage:
    def __init__(self, rights):
        self.rights = rights
        self.upserts = []
        self.calls = 0

    def entitlements(self, *, state):
        assert state == "active"
        self.calls += 1
        return self.rights

    def catalog_entries_by_ids(self, ids):
        assert len(ids) == 1
        return {}

    def catalog_entries_upsert_many(self, rows):
        self.upserts.extend(rows)


def item(provider="fictional-a"):
    return SearchResult(
        id="item-1", record_id="book-1", docbase="BOOKS", medium="digital",
        title="Un livre", author="Auteure", type=None, format="epub",
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="PERSONAL",
        source_provider=provider, source_credential="card-a",
        source_account_id="account-a", source_collection="personal",
        revision_id="revision-a", owned=True,
    )


def right(provider="fictional-a", **changes):
    value = {
        "identity_id": "alice", "provider_id": provider,
        "credential_id": "card-a", "account_id": "account-a",
        "item_id": "book-1", "revision_id": "revision-a",
        "entitlement_type": "owned", "state": "active",
    }
    value.update(changes)
    return value


def projection(book, rights, *, active="alice", asked="alice"):
    storage = Storage(rights)
    service = NS(
        current_identity=NS(identity_id=active), storage=storage,
        _account_context_epoch=5, _source_context_epoch=7,
    )
    assert _mirror_remote_results(service, [book], identity_id=asked) == 1
    assert len(storage.upserts) == 1
    assert storage.calls == 1
    return storage.upserts[0]["local_state"]


@pytest.mark.parametrize("provider", ["fictional-a", "fictional-b"])
def test_exact_personal_ledger_required_for_every_provider(provider):
    row = item(provider)
    assert projection(row, []) == "remote_catalog"
    assert projection(row, [right(provider)]) == "remote_owned"


@pytest.mark.parametrize("bad", [
    {"identity_id": "bob"}, {"provider_id": "fictional-b"},
    {"credential_id": "foreign"}, {"account_id": "foreign"},
    {"item_id": "foreign"}, {"revision_id": "foreign"},
    {"entitlement_type": "loan"}, {"state": "archived"},
])
def test_search_owned_hint_never_bypasses_exact_right_scope(bad):
    assert projection(item(), [right(**bad)]) == "remote_catalog"


def test_public_stale_or_other_identity_mirrors_never_gain_ownership():
    exact = right()
    public = replace(item(), source_collection="catalog")
    stale = replace(item(), metadata={"search_provenance": "known_catalog_stale"})
    assert projection(public, [exact]) == "remote_catalog"
    assert projection(stale, [exact]) == "remote_catalog"
    assert projection(item(), [exact], active="bob") == "remote_catalog"
    assert projection(item(), [exact], asked="bob") == "remote_catalog"


def test_no_provider_filter_or_fallback_in_mirror_source():
    from inspect import getsource
    source = getsource(_mirror_remote_results)
    assert "snapshot_search_owned_evidence(self)" in source
    assert "search_result_has_verified_purchase(" in source
    assert 'row.source_provider not in {"kobo", "numilog"}' not in source
