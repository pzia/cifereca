from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cifereca.paths import config_dir


@dataclass(slots=True)
class PluginStateStore:
    path: Path = field(default_factory=lambda: config_dir() / "plugins-state.json")
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            self.data = {"enabled": {}, "priority": {}, "active_packages": {}, "identity_enabled": {}}
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        self.data = value if isinstance(value, dict) else {}
        self.data.setdefault("enabled", {})
        self.data.setdefault("priority", {})
        self.data.setdefault("active_packages", {})
        self.data.setdefault("identity_enabled", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def enabled_override(self, plugin_id: str) -> bool | None:
        value = self.data.get("enabled", {}).get(plugin_id)
        return value if isinstance(value, bool) else None

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        self.data.setdefault("enabled", {})[plugin_id] = bool(enabled)
        self.save()

    def identity_enabled_override(self, plugin_id: str, identity_id: str) -> bool | None:
        plugin = self.data.get("identity_enabled", {}).get(plugin_id, {})
        value = plugin.get(identity_id) if isinstance(plugin, dict) else None
        return value if isinstance(value, bool) else None

    def set_identity_enabled(self, plugin_id: str, identity_id: str, enabled: bool | None) -> None:
        rows = self.data.setdefault("identity_enabled", {}).setdefault(plugin_id, {})
        if enabled is None:
            rows.pop(identity_id, None)
        else:
            rows[identity_id] = bool(enabled)
        self.save()

    def priority_override(self, plugin_id: str) -> int | None:
        value = self.data.get("priority", {}).get(plugin_id)
        return int(value) if isinstance(value, int) else None

    def set_priority(self, plugin_id: str, priority: int | None) -> None:
        rows = self.data.setdefault("priority", {})
        if priority is None:
            rows.pop(plugin_id, None)
        else:
            rows[plugin_id] = int(priority)
        self.save()

    def active_package(self, package_id: str) -> str | None:
        value = self.data.get("active_packages", {}).get(package_id)
        return str(value) if value else None

    def set_active_package(self, package_id: str, version: str | None) -> None:
        rows = self.data.setdefault("active_packages", {})
        if version:
            rows[package_id] = str(version)
        else:
            rows.pop(package_id, None)
        self.save()
