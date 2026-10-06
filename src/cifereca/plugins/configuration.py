from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from cifereca.paths import config_dir


class PluginConfigError(ValueError):
    pass


_ALLOWED_SCOPES = {"plugin", "instance", "identity", "target"}


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True


def _validate_scalar(name: str, value: Any, schema: Mapping[str, Any]) -> None:
    expected = str(schema.get("type") or "").strip()
    if expected and not _type_ok(value, expected):
        raise PluginConfigError(f"{name}: expected {expected}")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise PluginConfigError(f"{name}: value is not in enum")
    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise PluginConfigError(f"{name}: string is too short")
        if isinstance(max_length, int) and len(value) > max_length:
            raise PluginConfigError(f"{name}: string is too long")
        fmt = str(schema.get("format") or "").strip().lower()
        if fmt in {"uri", "url"} and value:
            parsed = urlparse(value)
            if not parsed.scheme or (parsed.scheme in {"http", "https"} and not parsed.netloc):
                raise PluginConfigError(f"{name}: invalid URI")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise PluginConfigError(f"{name}: below minimum")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise PluginConfigError(f"{name}: above maximum")
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_scalar(f"{name}[{index}]", item, item_schema)


def schema_scope(schema: Mapping[str, Any]) -> str:
    scope = str(schema.get("x-cifereca-scope") or "plugin").strip().lower()
    if scope not in _ALLOWED_SCOPES:
        raise PluginConfigError(f"unsupported configuration scope: {scope}")
    return scope


def defaults_for_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    out: dict[str, Any] = {}
    for name, spec in properties.items():
        if isinstance(spec, dict) and "default" in spec:
            out[str(name)] = deepcopy(spec["default"])
    return out


def validate_plugin_config(schema: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    if not schema:
        if not isinstance(values, Mapping):
            raise PluginConfigError("configuration must be an object")
        return dict(values)
    if str(schema.get("type") or "object") != "object":
        raise PluginConfigError("plugin configuration root must be type=object")
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        raise PluginConfigError("properties must be an object")
    required = schema.get("required") or []
    if not isinstance(required, list):
        raise PluginConfigError("required must be a list")
    merged = defaults_for_schema(schema)
    merged.update(dict(values))
    for name in required:
        key = str(name)
        if key not in merged or merged[key] in (None, ""):
            raise PluginConfigError(f"{key}: required value missing")
    allow_extra = schema.get("additionalProperties", False)
    for name, value in merged.items():
        spec = properties.get(name)
        if spec is None:
            if allow_extra is not True:
                raise PluginConfigError(f"{name}: unknown configuration key")
            continue
        if not isinstance(spec, dict):
            raise PluginConfigError(f"{name}: invalid schema")
        _validate_scalar(str(name), value, spec)
    return merged


@dataclass(slots=True)
class PluginConfigStore:
    path: Path = field(default_factory=lambda: config_dir() / "plugins-config.json")
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            self.data = {"version": 1, "values": {}}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        self.data = raw if isinstance(raw, dict) else {}
        self.data.setdefault("version", 1)
        self.data.setdefault("values", {})
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _scope_key(scope: str, scope_id: str | None) -> str:
        if scope == "plugin":
            return "_"
        value = str(scope_id or "").strip()
        if not value:
            raise PluginConfigError(f"scope_id required for scope={scope}")
        return value

    def raw(self, plugin_id: str, *, scope: str = "plugin", scope_id: str | None = None) -> dict[str, Any]:
        if scope not in _ALLOWED_SCOPES:
            raise PluginConfigError(f"unsupported configuration scope: {scope}")
        key = self._scope_key(scope, scope_id)
        plugin = self.data.get("values", {}).get(plugin_id, {})
        bucket = plugin.get(scope, {}) if isinstance(plugin, dict) else {}
        value = bucket.get(key, {}) if isinstance(bucket, dict) else {}
        return dict(value) if isinstance(value, dict) else {}

    def get(self, plugin_id: str, schema: Mapping[str, Any], *, scope_id: str | None = None) -> dict[str, Any]:
        scope = schema_scope(schema)
        return validate_plugin_config(schema, self.raw(plugin_id, scope=scope, scope_id=scope_id))

    def set(self, plugin_id: str, schema: Mapping[str, Any], values: Mapping[str, Any], *, scope_id: str | None = None) -> dict[str, Any]:
        scope = schema_scope(schema)
        validated = validate_plugin_config(schema, values)
        key = self._scope_key(scope, scope_id)
        plugin = self.data.setdefault("values", {}).setdefault(plugin_id, {})
        bucket = plugin.setdefault(scope, {})
        bucket[key] = dict(validated)
        self.save()
        return dict(validated)

    def scope_ids(self, plugin_id: str, schema: Mapping[str, Any]) -> list[str]:
        scope = schema_scope(schema)
        if scope == "plugin":
            return ["_"]
        plugin = self.data.get("values", {}).get(plugin_id, {})
        bucket = plugin.get(scope, {}) if isinstance(plugin, dict) else {}
        if not isinstance(bucket, dict):
            return []
        return sorted(str(key) for key in bucket)

    def clear(self, plugin_id: str, *, scope: str = "plugin", scope_id: str | None = None) -> None:
        key = self._scope_key(scope, scope_id)
        plugin = self.data.setdefault("values", {}).get(plugin_id)
        if not isinstance(plugin, dict):
            return
        bucket = plugin.get(scope)
        if isinstance(bucket, dict):
            bucket.pop(key, None)
        self.save()


__all__ = [
    "PluginConfigError", "PluginConfigStore", "defaults_for_schema",
    "schema_scope", "validate_plugin_config",
]
