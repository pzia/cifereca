from __future__ import annotations

from cifereca.plugin_api import CatalogServiceFactory, check_service_contract, contract_definition
from cifereca.plugins.categories import CategoryRegistry, CORE_CATEGORIES


class Factory:
    def create(self, **options):
        return {"options": options}


class NotFactory:
    pass


def test_catalog_service_is_a_distinct_core_category():
    categories = {row.id: row for row in CORE_CATEGORIES}

    assert "catalog.service" in categories
    category = categories["catalog.service"]
    assert category.resolution == "priority"
    assert category.contract == "cifereca.catalog.service/1"
    assert "Dépendance technique" in category.description
    assert category.id != "provider.library"


def test_catalog_service_has_a_public_typed_contract():
    definition = contract_definition("catalog.service")

    assert definition is not None
    assert definition.contract_id == "cifereca.catalog.service"
    assert definition.version == "1"
    assert definition.protocol_name == "CatalogServiceFactory"
    assert isinstance(Factory(), CatalogServiceFactory)
    assert check_service_contract("catalog.service", Factory()).ok


def test_catalog_service_rejects_objects_without_factory_contract():
    result = check_service_contract("catalog.service", NotFactory())

    assert result.ok is False
    assert result.reason_code == "contract_mismatch"
    assert result.detail["protocol"] == "CatalogServiceFactory"


def test_category_registry_exposes_catalog_service_without_plugin_extension():
    registry = CategoryRegistry()
    category = registry.get("catalog.service")

    assert category.display_name == "Service de catalogue"
    assert category.contract_id == "cifereca.catalog.service"
    assert registry.get("provider.library").contract_id == "cifereca.provider"


def test_catalog_service_factory_is_exported_from_public_api():
    from cifereca import plugin_api

    assert plugin_api.CatalogServiceFactory is CatalogServiceFactory
