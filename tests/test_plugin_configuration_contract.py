from __future__ import annotations

import json
import os
from pathlib import Path
import zipfile

import pytest

from cifereca.plugins import (
    PackageStore,
    PluginConfigError,
    PluginConfigStore,
    PluginManager,
    PluginStateStore,
)
from cifereca.plugins.configuration import defaults_for_schema, validate_plugin_config


def _manager(tmp_path: Path) -> PluginManager:
    return PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "plugins-state.json"),
        config_store=PluginConfigStore(tmp_path / "plugins-config.json"),
    )


def _archive(tmp_path: Path, name: str, manifest: dict, module_name: str, source: str) -> Path:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        bundle.writestr(module_name, source)
    return archive


def test_plugin_configuration_defaults_validation_and_private_store(tmp_path: Path):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "format": "uri", "default": "https://example.invalid"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 60, "default": 10},
            "token": {"type": "string", "x-cifereca-secret": True},
        },
        "required": ["url"],
    }

    assert defaults_for_schema(schema) == {"url": "https://example.invalid", "timeout": 10}
    assert validate_plugin_config(schema, {}) == {
        "url": "https://example.invalid",
        "timeout": 10,
    }
    with pytest.raises(PluginConfigError, match="invalid URI"):
        validate_plugin_config(schema, {"url": "not a uri"})
    with pytest.raises(PluginConfigError, match="unknown configuration key"):
        validate_plugin_config(schema, {"extra": True})

    store = PluginConfigStore(tmp_path / "plugins-config.json")
    saved = store.set(
        "test.plugin",
        schema,
        {"url": "https://example.org", "timeout": 12, "token": "secret"},
    )
    assert saved["token"] == "secret"
    assert store.get("test.plugin", schema)["timeout"] == 12
    if os.name == "posix":
        assert (store.path.stat().st_mode & 0o777) == 0o600


def test_required_plugin_configuration_blocks_before_import_then_activates(tmp_path: Path):
    sentinel = tmp_path / "IMPORTED"
    manifest = {
        "manifest": 1,
        "package": {
            "id": "org.test.configured",
            "version": "1.0.0",
            "name": "Configured",
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [{
            "id": "test.configured",
            "type": "recommendation",
            "entrypoint": "configured:PLUGIN",
            "config_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"endpoint": {"type": "string", "format": "uri"}},
                "required": ["endpoint"],
            },
        }],
    }
    source = (
        "from pathlib import Path\n"
        "class P:\n"
        " def activate(self, ctx):\n"
        f"  Path({str(sentinel)!r}).write_text(ctx.config['endpoint'])\n"
        "  return dict(ctx.config)\n"
        " def deactivate(self): pass\n"
        "PLUGIN=P()\n"
    )
    archive = _archive(tmp_path, "configured.zip", manifest, "configured.py", source)

    manager = _manager(tmp_path)
    try:
        manager.install(archive)
        status = manager.status("test.configured")
        assert status.state == "blocked"
        assert status.reason == "configuration"
        assert status.reason_code == "configuration_missing"
        assert not sentinel.exists()

        manager.set_plugin_config("test.configured", {"endpoint": "https://example.org"})
        assert manager.status("test.configured").state == "ready"
        assert sentinel.read_text(encoding="utf-8") == "https://example.org"
    finally:
        manager.close()


def test_identity_scoped_configuration_is_resolved_lazily_per_identity(tmp_path: Path):
    manifest = {
        "manifest": 1,
        "package": {
            "id": "org.test.scoped",
            "version": "1.0.0",
            "name": "Scoped",
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [{
            "id": "test.scoped",
            "type": "provider.library",
            "entrypoint": "scoped:PLUGIN",
            "config_schema": {
                "type": "object",
                "x-cifereca-scope": "identity",
                "additionalProperties": False,
                "properties": {"label": {"type": "string"}},
                "required": ["label"],
            },
        }],
    }
    source = (
        "class S:\n"
        " def __init__(self, ctx): self.ctx=ctx\n"
        " def for_identity(self, identity_id): return dict(self.ctx.configuration(identity_id))\n"
        "class P:\n"
        " def activate(self, ctx): return S(ctx)\n"
        " def deactivate(self): pass\n"
        "PLUGIN=P()\n"
    )
    archive = _archive(tmp_path, "scoped.zip", manifest, "scoped.py", source)

    manager = _manager(tmp_path)
    try:
        manager.install(archive)
        assert manager.status("test.scoped").state == "ready"

        with pytest.raises(PluginConfigError, match="required"):
            manager.service("test.scoped").for_identity("alice")

        manager.set_plugin_config("test.scoped", {"label": "Alice"}, scope_id="alice")
        assert manager.service("test.scoped").for_identity("alice") == {"label": "Alice"}

        with pytest.raises(PluginConfigError, match="required"):
            manager.service("test.scoped").for_identity("bob")
    finally:
        manager.close()
