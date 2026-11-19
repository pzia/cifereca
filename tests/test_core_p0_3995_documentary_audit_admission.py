"""Only repaired installed documentary audits may leave the Maintenance hold."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import zipfile

import pytest

from cifereca.brokers import CatalogBrokerImpl
from cifereca.plugin_api import MaintenanceContext
from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from tests.test_core043_progress_cache_stats import _service

ROOT = Path(__file__).resolve().parents[1] / "src/plugins/maintenance-library-audit"
TOOLS = ("maintenance.audit.covers", "maintenance.audit.languages")


def _archive(tmp_path, version, package_id="org.cifereca.maintenance.library-audit"):
    manifest = json.loads((ROOT / "cifereca-plugin.json").read_text())
    manifest["package"].update(id=package_id, version=version)
    for descriptor in manifest["plugins"]:
        descriptor["version"] = version
    path = tmp_path / "tools.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("cifereca-plugin.json", json.dumps(manifest))
        for source in ROOT.glob("*.py"):
            archive.writestr(source.name, source.read_bytes())
    return path


def _manager(tmp_path, broker):
    return PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "plugins.json"),
        brokers={"catalog": broker}, force_plugins=TOOLS,
    )


@pytest.mark.parametrize("version,package_id,broker", [
    ("0.2.10", "org.cifereca.maintenance.library-audit", NS(
        library_cover_audit=lambda **kwargs: {}, library_language_audit=lambda **kwargs: {})),
    ("0.3.0", "org.cifereca.maintenance.library-audit", NS(
        library_cover_audit=lambda **kwargs: {}, library_language_audit=lambda **kwargs: {})),
    ("0.2.11", "org.test.unknown-audit", NS(
        library_cover_audit=lambda **kwargs: {}, library_language_audit=lambda **kwargs: {})),
    ("0.2.11", "org.cifereca.maintenance.library-audit", NS()),
])
def test_old_unknown_or_missing_contract_stays_held_even_when_forced(
    tmp_path, version, package_id, broker,
):
    manager = _manager(tmp_path, broker)
    try:
        manager.install(_archive(tmp_path, version, package_id))
        manager.activate()
        for identifier in TOOLS:
            status = manager.status(identifier)
            assert not status.enabled and status.reason_code == "maintenance_safety_hold"
            with pytest.raises(ValueError, match="temporarily disabled"):
                manager.set_enabled(identifier, True)
    finally:
        manager.close()


def test_repaired_installed_tools_use_real_scoped_broker_plan_and_run(tmp_path):
    service = _service(tmp_path)
    broker = CatalogBrokerImpl(service)
    manager = _manager(tmp_path, broker)
    try:
        service.catalog_scan = Mock(side_effect=AssertionError("unexpected scan"))
        service.storage.catalog_entry_put({
            "catalog_entry_id": "a", "item_id": "a", "identity_id": "alice",
            "provider_id": "local", "local_state": "local", "title": "A",
            "media_format": "pdf", "metadata": {"language": "FR"},
        })
        manager.install(_archive(tmp_path, "0.2.11"))
        manager.activate()
        class Control:
            def check_cancelled(self): pass
            def report(self, **kwargs): pass
        for identifier, query in zip(TOOLS, (broker.library_cover_audit, broker.library_language_audit)):
            assert manager.status(identifier).enabled
            tool = manager.service(identifier)
            assert tool is not None
            plan = tool.plan(MaintenanceContext(scope="library", identity_id="alice"))
            assert plan.facts == query(identity_id="alice")
            assert plan.facts["schema"] == 2 and plan.facts["entries"] == 1
            result = tool.run(plan, Control())
            assert result.ok and result.changed == 0
            assert result.facts == plan.facts
            absent = tool.plan(MaintenanceContext(scope="library", identity_id="bob"))
            assert absent.facts["status"] == "unavailable"
            assert not tool.run(absent, Control()).ok
            manager.set_enabled(identifier, True)
        for identifier in ("maintenance.audit.naming", "maintenance.audit.integrity",
                           "maintenance.repair.naming", "maintenance.cleanup.duplicates"):
            assert not manager.status(identifier).enabled
            assert manager.status(identifier).reason_code == "maintenance_safety_hold"
        manager.refresh()
        manager.activate()
        assert all(manager.status(identifier).enabled for identifier in TOOLS)
        service.catalog_scan.assert_not_called()
        assert service.storage.entitlements() == [] and service.storage.acquisitions() == []
    finally:
        manager.close()
        service.close()
