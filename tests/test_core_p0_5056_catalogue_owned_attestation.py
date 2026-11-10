"""#5056: unknown providers cannot mint 'remote_owned' from search flags.

No network, provider session, or write: rights must be independent, exact
Core ledger witnesses. This gate covers arbitrary new provider IDs, not just
historical Kobo/Numilog.
"""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.catalogue_owned_projection import attested_catalogue_owned_keys
from cifereca.models import SearchResult


def row(provider, credential, edition, owned=True):
    return SearchResult(
        id=edition, record_id=edition, docbase="BOOKS", medium="digital",
        title="Synthetic title", author=None, type=None, format=None,
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="CATALOGUE",
        source_provider=provider, source_credential=credential, owned=owned,
    )


def test_unknown_provider_claim_requires_exact_existing_owned_entitlement():
    storage = NS(entitlements=Mock(return_value=[
        {"identity_id": "alice", "credential_id": "card-a",
         "item_id": "book-a", "entitlement_type": "owned"},
    ]))
    a = row("provider-future", "card-a", "book-a")
    assert attested_catalogue_owned_keys(storage, [a], identity_id="alice") == {
        ("provider-future", "alice", "card-a", "book-a"),
    }
    storage.entitlements.assert_called_once_with(
        provider_id="provider-future", state="active",
    )


@pytest.mark.parametrize("wrong", [
    {"identity_id": "bob", "credential_id": "card-a", "item_id": "book-a",
     "entitlement_type": "owned"},
    {"identity_id": "alice", "credential_id": "card-b", "item_id": "book-a",
     "entitlement_type": "owned"},
    {"identity_id": "alice", "credential_id": "card-a", "item_id": "book-b",
     "entitlement_type": "owned"},
    {"identity_id": "alice", "credential_id": "card-a", "item_id": "book-a",
     "entitlement_type": "loan"},
    {"identity_id": "alice", "credential_id": "card-a", "item_id": "book-a",
     "entitlement_type": "unknown"},
    {"identity_id": "alice", "credential_id": None, "item_id": "book-a",
     "entitlement_type": "owned"},
])
def test_foreign_or_unverified_entitlement_cannot_promote_a_catalogue_row(wrong):
    storage = NS(entitlements=Mock(return_value=[wrong]))
    assert not attested_catalogue_owned_keys(
        storage, [row("new-plugin", "card-a", "book-a")], identity_id="alice",
    )


def test_public_results_and_entitlement_read_failure_are_non_authorizing():
    storage = NS(entitlements=Mock(side_effect=RuntimeError("private token")))
    assert not attested_catalogue_owned_keys(
        storage, [row("new-plugin", "card-a", "book-a")], identity_id="alice",
    )
    storage.entitlements.reset_mock()
    assert not attested_catalogue_owned_keys(
        storage, [row("new-plugin", "public", "book-a", owned=False)],
        identity_id="alice",
    )
    storage.entitlements.assert_not_called()


def test_provider_ledger_lookup_cannot_splice_rights_across_providers():
    def entitlements(*, provider_id, state):
        assert state == "active"
        if provider_id == "provider-a":
            return [{"identity_id": "alice", "credential_id": "card",
                     "item_id": "book", "entitlement_type": "owned"}]
        return []
    storage = NS(entitlements=entitlements)
    assert attested_catalogue_owned_keys(storage, [
        row("provider-a", "card", "book"),
        row("provider-b", "card", "book"),
    ], identity_id="alice") == {("provider-a", "alice", "card", "book")}


def test_search_orchestration_must_verify_every_vendor_not_bypass_unknowns():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/core/search_orchestration.py").read_text()
    method = source.split("def _mirror_remote_results(", 1)[1].split("\ndef ", 1)[0]
    assert "attested_catalogue_owned_keys(" in method
    assert 'row.source_provider not in {"kobo", "numilog"}' not in method
    assert "row.owned is True" in method
