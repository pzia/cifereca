"""P0 #5056: purchased catalogue mirror needs independently stored rights."""
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from cifereca.core.search_orchestration import _mirror_remote_results
from cifereca.models import SearchResult


class Storage:
    def __init__(self, rights):
        self.rights = rights
        self.upserts = []
    def entitlements(self, *, provider_id=None, state):
        assert state == "active"
        return [x for x in self.rights if provider_id is None
                or x.get("provider_id") == provider_id]
    def catalog_entries_by_ids(self, ids):
        return {}
    def catalog_entries_upsert_many(self, rows):
        self.upserts.extend(rows)


def book(provider="fictional-a", account="acc", revision="revision"):
    return SearchResult(
        id="x", record_id="book", docbase="BOOKS", medium="digital",
        title="Livre", author="Auteur", type=None, format="epub",
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="PERSONAL",
        source_provider=provider, source_credential="card",
        source_account_id=account, revision_id=revision,
        source_collection="library", owned=True,
    )


def right(provider="fictional-a", **changes):
    d = dict(provider_id=provider, identity_id="alice",
             credential_id="card", account_id="acc", item_id="book",
             revision_id="revision", entitlement_type="owned", state="active")
    d.update(changes)
    return d


def mirror(row, rights, identity="alice"):
    storage = Storage(rights)
    service = NS(storage=storage, current_identity=NS(identity_id="alice"))
    assert _mirror_remote_results(service, [row], identity_id=identity) == 1
    return storage.upserts[0]["local_state"]


@pytest.mark.parametrize("provider", ["fictional-a", "fictional-b"])
def test_provider_neutral_positive_and_missing_right(provider):
    assert mirror(book(provider), []) == "remote_catalog"
    assert mirror(book(provider), [right(provider)]) == "remote_owned"


@pytest.mark.parametrize("change", [
    dict(identity_id="bob"), dict(credential_id="other"),
    dict(account_id="other"), dict(item_id="other"),
    dict(revision_id="other"), dict(entitlement_type="loan"),
    dict(state="archived"),
])
def test_cross_account_right_or_loan_never_becomes_ownership(change):
    assert mirror(book(), [right(**change)]) == "remote_catalog"


@pytest.mark.parametrize("changes", [
    dict(source_collection="catalog"), dict(source_collection="public_catalog"),
    dict(source_credential="__public__"),
    dict(metadata={"search_provenance": "known_catalog_stale"}),
])
def test_public_or_stale_rows_remain_nonowned(changes):
    assert mirror(replace(book(), **changes), [right()]) == "remote_catalog"


def test_legacy_personal_inventory_without_account_or_revision():
    row = book(account=None, revision=None)
    assert mirror(row, [right()]) == "remote_owned"
    assert mirror(row, []) == "remote_catalog"
    from cifereca.core.search_owned_evidence import (
        snapshot_search_owned_evidence, search_result_has_verified_purchase,
    )
    service = NS(storage=Storage([right()]), current_identity=NS(identity_id="alice"))
    proof = snapshot_search_owned_evidence(service)
    assert proof is not None  # strict actionable evidence really evaluated
    assert not search_result_has_verified_purchase(
        row, proof, current_identity_id="alice",
    )


def test_every_provider_needs_ledger_without_hardcoded_exemption():
    import inspect
    source = inspect.getsource(_mirror_remote_results)
    assert "attested_catalogue_owned_keys(" in source
    assert 'row.source_provider not in {"kobo", "numilog"}' not in source
