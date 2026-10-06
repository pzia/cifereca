from __future__ import annotations

import json
from pathlib import Path

from cifereca.plugin_api import (
    ArtifactFormat, ArtifactRef, BookRef, SyncRequest, TransformCapability,
    UiActionContext,
)
from cifereca.plugins import PackageStore, PluginConfigStore, PluginManager, PluginStateStore


def _write_package(root: Path, *, package_id: str, plugin_id: str, category: str = "recommendation", priority: int = 0):
    pkg = root / "packages" / package_id / "1.0.0"
    pkg.mkdir(parents=True)
    (pkg / "plugin.py").write_text(
        "class Plugin:\n"
        "    def activate(self, context):\n"
        "        self.context=context\n"
        "        return self\n"
        "plugin=Plugin()\n",
        encoding="utf-8",
    )
    manifest = {
        "manifest": 1,
        "package": {"id": package_id, "version": "1.0.0", "name": package_id, "publisher": "tests", "plugin_api": ">=1,<2"},
        "plugins": [{
            "id": plugin_id, "type": category, "version": "1.0.0", "entrypoint": "plugin:plugin",
            "priority": priority, "capabilities": ["demo"], "requires_services": ["catalog"],
        }],
    }
    (pkg / "cifereca-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pkg / ".cifereca-install.json").write_text("{}", encoding="utf-8")
    pointer = pkg.parent / "current.json"
    pointer.write_text(json.dumps({"active": "1.0.0", "previous": None}), encoding="utf-8")


def _manager(tmp_path: Path, **kwargs):
    kwargs.setdefault("brokers", {"catalog": object()})
    return PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "state.json"),
        config_store=PluginConfigStore(tmp_path / "config.json"),
        **kwargs,
    )


def test_public_models_cover_core037_contract():
    fmt = ArtifactFormat("application/epub+zip", "epub")
    artifact = ArtifactRef("a1", "b1", fmt, 12, "abc")
    capability = TransformCapability("optimize", (fmt,), (fmt,), supports_profiles=True, supports_plan=True)
    assert capability.supports_plan
    assert UiActionContext(target="artifact", book=BookRef("b1"), artifact=artifact).artifact == artifact
    assert SyncRequest(identity_id="i1", deep=True).deep


def test_services_by_category_are_public_and_priority_ordered(tmp_path: Path):
    _write_package(tmp_path, package_id="org.test.one", plugin_id="recommendation.one", priority=10)
    _write_package(tmp_path, package_id="org.test.two", plugin_id="recommendation.two", priority=30)
    manager = _manager(tmp_path)
    manager.activate()
    rows = manager.services("recommendation")
    assert [row.plugin_id for row in rows] == ["recommendation.two", "recommendation.one"]
    assert rows[0].category_id == "recommendation"
    service = manager.service("recommendation.one")
    assert service.context.services("recommendation")[0].plugin_id == "recommendation.two"
    assert service.context.broker("catalog") is not None
    assert service.context.logging_level > 0


def test_no_plugins_and_force_plugin_policy(tmp_path: Path):
    _write_package(tmp_path, package_id="org.test.one", plugin_id="recommendation.one")
    _write_package(tmp_path, package_id="org.test.two", plugin_id="recommendation.two")
    manager = _manager(tmp_path, disable_plugins=True, force_plugins=("recommendation.two",))
    statuses = {row.descriptor.id: row for row in manager.activate()}
    assert statuses["recommendation.one"].enabled is False
    assert statuses["recommendation.two"].enabled is True
    assert manager.service("recommendation.one") is None
    assert manager.service("recommendation.two") is not None


def test_service_subscription_is_idempotently_unsubscribable(tmp_path: Path):
    _write_package(tmp_path, package_id="org.test.one", plugin_id="recommendation.one")
    manager = _manager(tmp_path)
    calls = []
    unsubscribe = manager.subscribe_services("recommendation", lambda: calls.append("changed"))
    manager.activate()
    assert calls
    unsubscribe(); unsubscribe()
    count = len(calls)
    manager.activate()
    assert len(calls) == count

def test_isolated_plugin_static_check_rejects_private_core_import(tmp_path: Path):
    from cifereca.plugin_test_runner import private_imports
    (tmp_path / "bad.py").write_text("from cifereca.diagnostics import redact_text\n", encoding="utf-8")
    assert private_imports(tmp_path) == ["bad.py:1: cifereca.diagnostics"]
