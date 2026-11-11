"""#5054: a private catalogue observation is an origin, not an entitlement.

Provider-neutral fixtures prove Library visibility and exact source selection,
without any historical rights row or GET during rendering.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from cifereca.core import selected_source_offer as offers
from tests.test_sync_state_contract import _service


def _register(service, *, credential="card-1", account_id="borrower-1",
              edition="edition-1", state="remote_catalog",
              collection="library", scoped=2):
    account = Account(
        identity_id="default", provider_id="fictional-provider",
        credential_id=credential, unique_id=account_id,
        display_name="Fixture",
    )
    service.accounts.append(account)
    connection = service.storage.provider_connection_put({
        "identity_id": "default", "provider_id": "fictional-provider",
        "credential_id": credential, "enabled": True,
    })
    service.storage.provider_account_put({
        "connection_id": connection, "identity_id": "default",
        "provider_id": "fictional-provider", "credential_id": credential,
        "account_id": account_id, "label": "Fixture",
    })
    class PurchaseOfferSession:
        selected_purchase_branch = "private-purchases"
    service._sessions[
        service._session_key("fictional-provider", credential)
    ] = PurchaseOfferSession()
    service.storage.catalog_entries_upsert_many([{
        "catalog_entry_id": f"private:{credential}:{edition}",
        "identity_id": "default", "provider_id": "fictional-provider",
        "item_id": edition, "local_state": state, "title": "Remote book",
        "author": "Synthetic author", "media_format": "epub",
        "metadata": {
            "source_credential": credential, "source_collection": collection,
            "catalogue_scope_version": scoped, "publisher": "Fictional Press",
        },
    }])
    return account


def test_private_remote_observation_visible_without_entitlement(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        _register(service)
        assert service.storage.entitlements(provider_id="fictional-provider") == []
        cards = service.library_entries(identity_id="default")
        observed = [card for card in cards
                    if card.get("source") == "private_catalog_observation"]
        assert len(observed) == 1
        card = observed[0]
        assert card["local"] is False and card["state"] == "remote_catalog"
        assert card["entitlement_type"] is None and card["entitlement_id"] is None
        assert card["possessions"] == []
        assert card["placement_count"] == 0
        assert card["source_refs"] == [{
            "identity_id": "default", "provider_id": "fictional-provider",
            "credential_id": "card-1", "account_id": "borrower-1",
            "item_id": "edition-1", "kind": "purchase",
        }]
        sentinel = object()
        observed_calls = []
        monkeypatch.setattr(offers, "select_source_offer_target",
            lambda _service, **scope: observed_calls.append(scope) or sentinel)
        assert offers.select_library_source_offer_target(service, card) is sentinel
        assert observed_calls == [{
            "provider_id": "fictional-provider",
            "credential_id": "card-1", "item_id": "edition-1",
        }]
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()


def test_same_edition_two_accounts_stays_distinct_without_rights(tmp_path):
    service = _service(tmp_path)
    try:
        _register(service, credential="card-1", account_id="borrower-1")
        _register(service, credential="card-2", account_id="borrower-2")
        cards = [row for row in service.library_entries(identity_id="default")
                 if row.get("source") == "private_catalog_observation"]
        assert len(cards) == 2
        assert {row["source_refs"][0]["credential_id"] for row in cards} == {
            "card-1", "card-2",
        }
        assert len({row["key"] for row in cards}) == 2
        assert service.storage.entitlements(provider_id="fictional-provider") == []
    finally:
        service.close()


@pytest.mark.parametrize("kwargs", [
    {"collection": "public_catalog"}, {"scoped": None},
    {"scoped": 1}, {"state": "available_to_buy"},
])
def test_public_ambiguous_or_unscoped_catalogue_never_enters_private_library(
    tmp_path, kwargs,
):
    service = _service(tmp_path)
    try:
        _register(service, **kwargs)
        assert not any(
            row.get("source") == "private_catalog_observation"
            for row in service.library_entries(identity_id="default")
        )
    finally:
        service.close()


def test_detached_account_cannot_be_selected_from_old_private_catalogue(tmp_path):
    service = _service(tmp_path)
    try:
        _register(service)
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "DELETE FROM provider_accounts WHERE provider_id=?",
                ("fictional-provider",),
            )
        assert not any(
            row.get("source") == "private_catalog_observation"
            for row in service.library_entries(identity_id="default")
        )
    finally:
        service.close()


def test_ambiguous_library_source_ref_never_selects_provider(monkeypatch):
    marker = object()
    seen = []
    monkeypatch.setattr(
        offers, "select_source_offer_target",
        lambda *_a, **_kw: seen.append(True) or marker,
    )
    service = NS(current_identity=NS(identity_id="default"), accounts=[])
    source = {
        "identity_id": "default", "provider_id": "fictional-provider",
        "credential_id": "card-1", "account_id": "borrower-1",
        "item_id": "edition-1", "kind": "purchase",
    }
    base = {
        "source": "private_catalog_observation", "state": "remote_catalog",
        "local": False, "source_refs": [source],
    }
    assert offers.select_library_source_offer_target(
        service, {**base, "source_refs": [source, source]},
    ) is None
    assert offers.select_library_source_offer_target(
        service, {**base, "source_refs": [{**source, "identity_id": "foreign"}]},
    ) is None
    assert offers.select_library_source_offer_target(
        service, {**base, "source": "entitlement"},
    ) is None
    assert seen == []


def test_private_loan_or_observation_never_fabricates_purchase_origin(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _register(service)
        service._sessions.clear()  # No current purchase-offer ABI.
        cards = [row for row in service.library_entries(identity_id="default")
                 if row.get("source") == "private_catalog_observation"]
        assert len(cards) == 1
        assert cards[0]["source_refs"][0]["kind"] == "observation"
        called = []
        monkeypatch.setattr(
            offers, "select_source_offer_target",
            lambda *_a, **_kw: called.append(True),
        )
        assert offers.select_library_source_offer_target(service, cards[0]) is None
        assert called == []
        assert service.storage.entitlements(provider_id="fictional-provider") == []
    finally:
        service.close()
