"""Public plugin API must not advertise retired automatic materialization."""
import cifereca.plugin_api as plugin_api


def test_public_exports_exclude_retired_source_materialization_schedule():
    assert "SourceMaterializationScheduleBroker" not in plugin_api.__all__
    assert not hasattr(plugin_api, "SourceMaterializationScheduleBroker")
    assert "SourceInventoryRefreshScheduleBroker" in plugin_api.__all__
    assert hasattr(plugin_api, "SourceInventoryRefreshScheduleBroker")


def test_public_plugin_star_import_resolves_every_advertised_symbol():
    namespace = {}
    exec("from cifereca.plugin_api import *", namespace)
    assert "SourceMaterializationScheduleBroker" not in namespace
    assert namespace["SourceInventoryRefreshScheduleBroker"] is (
        plugin_api.SourceInventoryRefreshScheduleBroker
    )
    assert set(plugin_api.__all__).issubset(namespace)
