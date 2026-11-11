"""#5048: local primary source selector is provider-neutral and read-only."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import SearchResult
from cifereca.core.primary_source_binding import configured_primary_source_current


def _item(provider):
    return SearchResult(
        id="synthetic-item", record_id="edition-a", docbase="TEST",
        medium="digital", title="Synthetic", author=None,
        type=None, format=None, publisher=None, published_at=None,
        description=None, thumbnail_url=None, friendly_url=None,
        scenario="TEST", source_provider=provider,
        source_credential="card-A",
    )


def _service(provider, *, selected=None):
    local = {
        "identity_id": "alice", "provider_id": provider,
        "credential_id": "card-A", "enabled": True,
    }
    configured = {
        "id": "card-A", "provider": provider,
        "identity_id": "alice", "enabled": True,
    }
    rows = [local]
    accounts = [configured]
    config = NS(
        identity=lambda _identity: {"primary_connection": selected},
        provider_accounts=lambda: accounts,
    )
    storage = NS(provider_connections=lambda *, identity_id: rows)
    return NS(config=config, storage=storage), rows, accounts


@pytest.mark.parametrize("provider", ["fixture-lender-a", "fixture-lender-b"])
def test_primary_connection_exact_source_credential_and_account(provider):
    service, rows, accounts = _service(provider, selected="card-A")
    item = _item(provider)
    check = lambda: configured_primary_source_current(
        service, item, "alice", provider_id=provider,
        selection_key="primary_connection",
    )
    assert check()
    assert not configured_primary_source_current(
        service, item, "alice", provider_id="foreign-provider",
        selection_key="primary_connection",
    )
    rows[0]["identity_id"] = "bob"
    assert not check()
    rows[0]["identity_id"] = "alice"
    accounts[0]["provider"] = "other-provider"
    assert not check()
    accounts[0]["provider"] = provider
    assert check()
    assert not configured_primary_source_current(
        service, replace(item, source_credential="card-B"), "alice",
        provider_id=provider, selection_key="primary_connection",
    )


def test_implicit_selection_never_chooses_first_of_two_sources():
    service, rows, _accounts = _service("fixture-lender", selected=None)
    item = _item("fixture-lender")
    assert configured_primary_source_current(
        service, item, "alice", provider_id="fixture-lender",
        selection_key="primary_connection",
    )
    rows.append(dict(rows[0], credential_id="card-B"))
    assert not configured_primary_source_current(
        service, item, "alice", provider_id="fixture-lender",
        selection_key="primary_connection",
    )


def test_shared_catalogue_is_never_a_borrow_action_source():
    service, _rows, _accounts = _service("fixture-lender", selected="card-A")
    item = replace(_item("fixture-lender"),
                   metadata={"catalogue_access": "shared_read_only"})
    assert not configured_primary_source_current(
        service, item, "alice", provider_id="fixture-lender",
        selection_key="primary_connection",
    )
