from __future__ import annotations

import json
from pathlib import Path

from cifereca.plugin_api import CONTRACT_REGISTRY, PLUGIN_API_VERSION
from cifereca.plugins import PackageStore, PluginConfigStore, PluginManager, PluginStateStore


def _package(root: Path, package_id: str, plugins: list[dict], modules: dict[str, str]):
    pkg = root / "packages" / package_id / "1.0.0"
    pkg.mkdir(parents=True)
    for name, source in modules.items():
        (pkg / name).write_text(source, encoding="utf-8")
    manifest = {
        "manifest": 1,
        "package": {"id": package_id, "version": "1.0.0", "plugin_api": ">=1.2,<2"},
        "plugins": plugins,
    }
    (pkg / "cifereca-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pkg / ".cifereca-install.json").write_text("{}", encoding="utf-8")
    (pkg.parent / "current.json").write_text(json.dumps({"active": "1.0.0", "previous": None}), encoding="utf-8")


def _manager(root: Path, brokers=None):
    return PluginManager(
        package_store=PackageStore(root / "packages"),
        state_store=PluginStateStore(root / "state.json"),
        config_store=PluginConfigStore(root / "config.json"),
        brokers=dict(brokers or {}),
    )


def test_plugin_api_12_and_public_contract_registry():
    assert PLUGIN_API_VERSION == "1.4.3"
    row = next(x for x in CONTRACT_REGISTRY if x.category == "document.transformer")
    assert row.contract_id == "cifereca.document.transformer"
    assert row.version == "1"
    assert "artifact.transform" in row.capabilities


def test_required_contract_activates_provider_before_consumer(tmp_path: Path):
    plugins = [
        {"id":"a.consumer","type":"recommendation","version":"1.0.0","entrypoint":"consumer:PLUGIN",
         "requires_contracts":[{"category":"device.profile","contract":"cifereca.device.profile","version":">=1,<2","capabilities":["device.profile.describe"]}]},
        {"id":"z.profile","type":"device.profile","version":"1.0.0","entrypoint":"profile:PLUGIN","capabilities":["device.profile.describe"]},
    ]
    _package(tmp_path, "org.test.contract-order", plugins, {
        "profile.py": "class P:\n def activate(self,c): return self\n def describe(self): return {'id':'x'}\n def deactivate(self): pass\nPLUGIN=P()\n",
        "consumer.py": "class P:\n def activate(self,c):\n  self.rows=c.services('device.profile',contract='cifereca.device.profile',version='>=1,<2',capabilities=('device.profile.describe',))\n  assert self.rows and self.rows[0].plugin_id=='z.profile'\n  return self\n def deactivate(self): pass\nPLUGIN=P()\n",
    })
    m=_manager(tmp_path); statuses={x.descriptor.id:x for x in m.activate()}
    assert statuses["z.profile"].state == "ready"
    assert statuses["a.consumer"].state == "ready"
    row=m.services("device.profile")[0]
    assert row.contract_id == "cifereca.device.profile" and row.contract_version == "1"
    assert row.capabilities == ("device.profile.describe",)
    assert row.registry_generation >= 1


def test_contract_mismatch_blocks_before_consumer_import(tmp_path: Path):
    marker=tmp_path / "IMPORTED"
    plugins=[{"id":"a.consumer","type":"recommendation","version":"1.0.0","entrypoint":"consumer:PLUGIN",
              "requires_contracts":[{"category":"device.profile","contract":"cifereca.device.profile","version":">=2,<3"}]}]
    _package(tmp_path,"org.test.contract-mismatch",plugins,{"consumer.py":f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\nclass P: pass\nPLUGIN=P()\n"})
    m=_manager(tmp_path); status={x.descriptor.id:x for x in m.activate()}["a.consumer"]
    assert status.state == "blocked" and status.reason_code == "contract_mismatch"
    assert not marker.exists()


def test_protocol_mismatch_is_incompatible_and_not_published(tmp_path: Path):
    plugins=[{"id":"ui.page.bad","type":"ui.page","version":"1.0.0","entrypoint":"bad:PLUGIN"}]
    _package(tmp_path,"org.test.bad-page",plugins,{"bad.py":"class P:\n def activate(self,c): return object()\n def deactivate(self): pass\nPLUGIN=P()\n"})
    m=_manager(tmp_path); status={x.descriptor.id:x for x in m.activate()}["ui.page.bad"]
    assert status.state == "incompatible" and status.reason_code == "contract_mismatch"
    assert not m.services("ui.page")


def test_degraded_contract_keeps_service_available(tmp_path: Path):
    plugins=[{"id":"recommendation.degraded","type":"recommendation","version":"1.0.0","entrypoint":"p:PLUGIN",
              "requires_contracts":[{"category":"device.profile","contract":"cifereca.device.profile","version":">=2","mode":"degraded"}]}]
    _package(tmp_path,"org.test.degraded",plugins,{"p.py":"class P:\n def activate(self,c): return self\n def deactivate(self): pass\nPLUGIN=P()\n"})
    m=_manager(tmp_path); status={x.descriptor.id:x for x in m.activate()}["recommendation.degraded"]
    assert status.state == "degraded"
    assert m.services("recommendation")[0].plugin_id == "recommendation.degraded"


def test_broker_requirements_are_separate_and_machine_readable(tmp_path: Path):
    plugins=[{"id":"recommendation.broker","type":"recommendation","version":"1.0.0","entrypoint":"p:PLUGIN","requires_brokers":["catalog"]}]
    _package(tmp_path,"org.test.broker",plugins,{"p.py":"class P: pass\nPLUGIN=P()\n"})
    m=_manager(tmp_path); status={x.descriptor.id:x for x in m.activate()}["recommendation.broker"]
    assert status.state == "blocked" and status.reason_code == "missing_broker"
    assert status.reason_detail["brokers"] == ("catalog",)


def test_registry_generation_only_changes_when_publication_changes(tmp_path: Path):
    plugins=[{"id":"recommendation.one","type":"recommendation","version":"1.0.0","entrypoint":"p:PLUGIN"}]
    _package(tmp_path,"org.test.generation",plugins,{"p.py":"class P:\n def activate(self,c): return self\n def deactivate(self): pass\nPLUGIN=P()\n"})
    m=_manager(tmp_path); calls=[]; m.subscribe_services("recommendation",lambda:calls.append(m.registry_generation))
    m.activate(); first=m.registry_generation; assert first >= 1 and len(calls)==1
    m.activate(); assert m.registry_generation == first and len(calls)==1


def test_core_categories_match_public_contract_registry():
    from cifereca.plugins.categories import CORE_CATEGORIES
    public={row.category:(row.contract_id,row.version) for row in CONTRACT_REGISTRY}
    assert all(public[row.id] == (row.contract_id,row.contract_version) for row in CORE_CATEGORIES)

def test_contract_test_kit_flags_legacy_capability_and_private_import(tmp_path: Path):
    from cifereca.plugin_contract_tests import check_capabilities, check_public_imports
    assert check_capabilities(("artifact.transform",))[0].ok
    assert check_capabilities(("legacy_name",))[0].code == "legacy_capability_name"
    (tmp_path / "bad.py").write_text("from cifereca.services import LibraryService\n", encoding="utf-8")
    result=check_public_imports(tmp_path)
    assert any((not row.ok and row.code == "private_core_import") for row in result)
