"""P1 #968: public virtual-provider collection contract, purely offline."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.plugin_api import (
    ProviderCollection, ProviderCollectionSnapshot, VirtualProviderEntry,
    ProviderCollectionValidationError, validate_provider_collection_snapshot,
    contract_definition, check_service_contract,
)
from cifereca.plugins.categories import CategoryRegistry


PARENT = "org.cifereca.integration.cifericaopds"


def child(source: str, *, catalog: int = 1, **changes):
    row = {
        "plugin_id": f"cifericaopds.{source}",
        "source_id": source,
        "capabilities": ["catalog.search"],
        "source_revision": "r1",
        "catalog_generation": catalog,
        "status": "ready",
        "kind": "catalog",
        "instantiation": "singleton_public",
        "parent_plugin_id": PARENT,
        "is_virtual": True,
    }
    row.update(changes)
    return row


def inventory(*rows, generation: int = 1):
    return {"discovery_generation": generation, "api_major": 1,
            "parent_plugin_id": PARENT, "children": list(rows)}


def checked(raw, **kw):
    return validate_provider_collection_snapshot(raw, parent_plugin_id=PARENT, **kw)


def test_new_public_contract_is_all_resolution_and_backward_compatible():
    category = CategoryRegistry().get("provider.collection")
    assert category.contract == "cifereca.provider.collection/1"
    assert category.resolution == "all"
    assert contract_definition("provider.collection").protocol_name == "ProviderCollection"
    assert CategoryRegistry().get("provider.library").contract == "cifereca.provider/1"


def test_two_virtual_sources_under_one_parent_are_immutable_and_separate():
    result = checked(inventory(child("alpha"), child("beta", catalog=8)))
    assert isinstance(result, ProviderCollectionSnapshot)
    assert {c.plugin_id for c in result.children} == {"cifericaopds.alpha", "cifericaopds.beta"}
    assert result.children[0].capabilities == ("catalog.search",)
    assert result.children[1].catalog_generation == 8
    assert result.children[0].instantiation == "singleton_public"
    assert checked(result) == result


@pytest.mark.parametrize("bad", [
    inventory(child("alpha"), child("alpha")),
    inventory(child("alpha", plugin_id="wrong.alpha")),
    inventory(child("../secret")),
    inventory(child("alpha", parent_plugin_id="wrong.parent")),
    inventory(child("alpha", instantiation="multiple")),
    inventory(child("alpha", is_virtual=False)),
    inventory(child("alpha", capabilities=["search"])),
    inventory(child("alpha", capabilities=["catalog.search", "catalog.search"])),
    inventory(child("alpha", catalog_generation=-1)),
    inventory(child("alpha", catalog_generation=True)),
    inventory(child("alpha", source_revision="")),
    inventory(child("alpha", source_id="BETA")),
    inventory(child("alpha", kind="<script>")),
    inventory(child("alpha"), generation=-1),
    {**inventory(child("alpha")), "api_major": 2},
    {**inventory(child("alpha")), "parent_plugin_id": "wrong"},
])
def test_invalid_inventories_are_rejected_before_any_publication(bad):
    with pytest.raises(ProviderCollectionValidationError):
        checked(bad)


def test_static_collision_and_replayed_generation_are_refused():
    with pytest.raises(ProviderCollectionValidationError, match="collision"):
        checked(inventory(child("alpha")), existing_provider_ids={"cifericaopds.alpha"})
    prior = checked(inventory(child("alpha", catalog=4), generation=7))
    assert checked(inventory(child("alpha", catalog=5), generation=8), previous=prior).children[0].catalog_generation == 5
    with pytest.raises(ProviderCollectionValidationError, match="stale"):
        checked(inventory(child("alpha"), generation=6), previous=prior)
    with pytest.raises(ProviderCollectionValidationError, match="stale"):
        checked(inventory(child("alpha", catalog=3), generation=8), previous=prior)
    with pytest.raises(ProviderCollectionValidationError, match="same generation"):
        checked(inventory(child("alpha", catalog=5), generation=7), previous=prior)
    # Existing virtual IDs from the last accepted snapshot are not static collisions.
    assert checked(inventory(child("alpha", catalog=5), generation=8),
                   previous=prior, existing_provider_ids={"cifericaopds.alpha"})


def test_invalid_second_child_never_returns_a_partial_first_child():
    prior = checked(inventory(child("alpha", catalog=2), generation=1))
    failed = inventory(child("alpha", catalog=3), child("beta", plugin_id="fake"), generation=2)
    with pytest.raises(ProviderCollectionValidationError):
        checked(failed, previous=prior)
    assert prior.children[0].catalog_generation == 2


def test_protocol_checks_shape_without_calling_network_or_factory():
    class Collection:
        def snapshot(self):
            return inventory(child("alpha"))

        def refresh(self, *, explicit=True):
            raise AssertionError("must not be called when validating contract")

        def create_provider(self, source_id):
            raise AssertionError("must not instantiate on contract validation")

        def subscribe(self, callback):
            return lambda: None

    col = Collection()
    assert isinstance(col, ProviderCollection)
    assert check_service_contract("provider.collection", col).ok
    assert checked(col.snapshot()).children[0].plugin_id == "cifericaopds.alpha"
    assert not check_service_contract("provider.collection", object()).ok


def test_inventory_has_finite_limits():
    raw = inventory(*(child(f"s{i}") for i in range(129)))
    with pytest.raises(ProviderCollectionValidationError, match="limit"):
        checked(raw)
