from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from cifereca.plugin_api import (
    CategoryDefinition,
    ContractRequirement,
    Dependency,
    MANIFEST_VERSION,
    PLUGIN_API_VERSION,
    PluginDescriptor,
    SystemRequirement,
    capability_name_valid,
    split_contract,
)

from .errors import ManifestError

MANIFEST_FILENAME = "cifereca-plugin.json"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")


def _id(value: object, field: str) -> str:
    result = str(value or "").strip().lower()
    if not _ID_RE.match(result):
        raise ManifestError(f"invalid {field}: {value!r}")
    return result


def _version(value: object, field: str) -> str:
    result = str(value or "").strip()
    try:
        Version(result)
    except InvalidVersion as exc:
        raise ManifestError(f"invalid {field}: {result!r}") from exc
    return result


def _specifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        return ""
    try:
        SpecifierSet(result)
    except InvalidSpecifier as exc:
        raise ManifestError(f"invalid {field}: {result!r}") from exc
    return result


def _dependencies(rows: object, field: str) -> tuple[Dependency, ...]:
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise ManifestError(f"{field} must be a list")
    out: list[Dependency] = []
    for index, row in enumerate(rows):
        if isinstance(row, str):
            out.append(Dependency(plugin=_id(row, f"{field}[{index}].plugin")))
            continue
        if not isinstance(row, dict):
            raise ManifestError(f"{field}[{index}] must be an object")
        out.append(Dependency(
            plugin=_id(row.get("plugin"), f"{field}[{index}].plugin"),
            version=_specifier(row.get("version"), f"{field}[{index}].version"),
        ))
    return tuple(out)



def _capabilities(value: object, field: str) -> tuple[str, ...]:
    # Manifest v1 historically allowed free capability strings. Keep them
    # readable in Plugin API 1.x; the contract test kit flags non-canonical
    # names so new publications use <domain>.<verb>[.<qualifier>].
    return _string_tuple(value, field)


def _contract_requirements(rows: object, field: str) -> tuple[ContractRequirement, ...]:
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise ManifestError(f"{field} must be a list")
    out: list[ContractRequirement] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ManifestError(f"{field}[{index}] must be an object")
        category = _id(row.get("category"), f"{field}[{index}].category")
        contract = str(row.get("contract") or "").strip()
        if not contract:
            raise ManifestError(f"{field}[{index}].contract is required")
        if "/" in contract:
            contract, embedded_version = split_contract(contract)
            if row.get("version") is None and embedded_version:
                row = dict(row)
                row["version"] = f"=={embedded_version}"
        mode = str(row.get("mode") or ("optional" if row.get("optional") else "required")).strip().lower()
        if mode not in {"required", "optional", "degraded"}:
            raise ManifestError(f"invalid {field}[{index}].mode: {mode!r}")
        out.append(ContractRequirement(
            category=category,
            contract=contract,
            version=_specifier(row.get("version"), f"{field}[{index}].version"),
            mode=mode,  # type: ignore[arg-type]
            capabilities=_capabilities(row.get("capabilities"), f"{field}[{index}].capabilities"),
        ))
    return tuple(out)

def _system_requirements(rows: object) -> tuple[SystemRequirement, ...]:
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise ManifestError("system_requires must be a list")
    out: list[SystemRequirement] = []
    allowed = {"binary", "file", "directory", "os", "python", "python-package"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ManifestError(f"system_requires[{index}] must be an object")
        kind = str(row.get("type") or "").strip().lower()
        if kind not in allowed:
            raise ManifestError(f"unsupported system requirement type: {kind!r}")
        # Core037-dev.2 compatibility: historical manifests sometimes used
        # {"type":"python","name":"Pillow"} to mean a Python distribution.
        # Keep interpreter constraints as type=python only when no name is given.
        if kind == "python" and str(row.get("name") or "").strip():
            kind = "python-package"
        if kind == "python-package" and not str(row.get("name") or "").strip():
            raise ManifestError(f"system_requires[{index}].name is required for python-package")
        args = row.get("version_args", ["--version"])
        if isinstance(args, str):
            args = [args]
        if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
            raise ManifestError(f"system_requires[{index}].version_args must be a list of strings")
        out.append(SystemRequirement(
            type=kind,
            name=str(row.get("name") or "").strip(),
            version=_specifier(row.get("version"), f"system_requires[{index}].version"),
            path=str(row.get("path") or "").strip(),
            version_args=tuple(args),
            import_name=str(row.get("import") or row.get("import_name") or "").strip(),
        ))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class PackageDefinition:
    id: str
    version: str
    name: str
    publisher: str
    plugin_api: str


@dataclass(frozen=True, slots=True)
class PluginPackageManifest:
    package: PackageDefinition
    categories: tuple[CategoryDefinition, ...]
    plugins: tuple[PluginDescriptor, ...]
    raw: Mapping[str, Any]

    def plugin(self, plugin_id: str) -> PluginDescriptor:
        return next(item for item in self.plugins if item.id == plugin_id)


def parse_manifest(data: Mapping[str, Any]) -> PluginPackageManifest:
    if int(data.get("manifest", 0) or 0) != MANIFEST_VERSION:
        raise ManifestError(f"unsupported manifest version: {data.get('manifest')!r}")
    package_raw = data.get("package")
    if not isinstance(package_raw, dict):
        raise ManifestError("manifest package object is required")
    package = PackageDefinition(
        id=_id(package_raw.get("id"), "package.id"),
        version=_version(package_raw.get("version"), "package.version"),
        name=str(package_raw.get("name") or package_raw.get("id") or "").strip(),
        publisher=str(package_raw.get("publisher") or "Unknown").strip(),
        plugin_api=_specifier(package_raw.get("plugin_api") or ">=1,<2", "package.plugin_api"),
    )
    if Version(PLUGIN_API_VERSION) not in SpecifierSet(package.plugin_api):
        raise ManifestError(
            f"package {package.id} {package.version} requires Plugin API {package.plugin_api}; "
            f"cifereca provides {PLUGIN_API_VERSION}"
        )

    categories: list[CategoryDefinition] = []
    category_rows = data.get("categories", [])
    if not isinstance(category_rows, list):
        raise ManifestError("categories must be a list")
    for index, row in enumerate(category_rows):
        if not isinstance(row, dict):
            raise ManifestError(f"categories[{index}] must be an object")
        resolution = str(row.get("resolution") or "priority").strip().lower()
        if resolution not in {"priority", "all", "single"}:
            raise ManifestError(f"invalid category resolution: {resolution}")
        categories.append(CategoryDefinition(
            id=_id(row.get("id"), f"categories[{index}].id"),
            display_name=str(row.get("display_name") or row.get("id") or "").strip(),
            contract=str(row.get("contract") or "").strip(),
            resolution=resolution,  # type: ignore[arg-type]
            description=str(row.get("description") or "").strip(),
        ))

    plugin_rows = data.get("plugins")
    if not isinstance(plugin_rows, list) or not plugin_rows:
        raise ManifestError("plugins must be a non-empty list")
    plugins: list[PluginDescriptor] = []
    seen: set[str] = set()
    for index, row in enumerate(plugin_rows):
        if not isinstance(row, dict):
            raise ManifestError(f"plugins[{index}] must be an object")
        plugin_id = _id(row.get("id"), f"plugins[{index}].id")
        if plugin_id in seen:
            raise ManifestError(f"duplicate plugin id in package: {plugin_id}")
        seen.add(plugin_id)
        entrypoint = str(row.get("entrypoint") or "").strip() or None
        type_id = _id(row.get("type"), f"plugins[{index}].type")
        if type_id != "meta.bundle" and not entrypoint:
            raise ManifestError(f"plugin {plugin_id} requires an entrypoint")
        plugin_version = _version(row.get("version") or package.version, f"plugins[{index}].version")
        schema = row.get("config_schema") or {}
        if not isinstance(schema, dict):
            raise ManifestError(f"plugins[{index}].config_schema must be an object")
        instantiation = str(row.get("instantiation") or "multiple").strip().lower().replace("-", "_")
        if instantiation == "singleton":
            instantiation = "singleton_public"
        if instantiation not in {"multiple", "per_identity", "singleton_public"}:
            raise ManifestError(f"invalid plugin instantiation mode: {instantiation}")
        plugins.append(PluginDescriptor(
            id=plugin_id,
            type=type_id,
            version=plugin_version,
            package_id=package.id,
            package_version=package.version,
            entrypoint=entrypoint,
            priority=int(row.get("priority", 0) or 0),
            requires=_dependencies(row.get("requires"), f"plugins[{index}].requires"),
            recommends=_dependencies(row.get("recommends"), f"plugins[{index}].recommends"),
            conflicts=_dependencies(row.get("conflicts"), f"plugins[{index}].conflicts"),
            system_requires=_system_requirements(row.get("system_requires")),
            enabled_by_default=bool(row.get("enabled_by_default", True)),
            display_name=str(row.get("display_name") or plugin_id).strip(),
            description=str(row.get("description") or "").strip(),
            config_schema=dict(schema),
            instantiation=instantiation,  # type: ignore[arg-type]
            help_text=_help_text(row.get("help_text")),
            capabilities=_capabilities(row.get("capabilities"), f"plugins[{index}].capabilities"),
            requires_brokers=_string_tuple(row.get("requires_brokers"), f"plugins[{index}].requires_brokers"),
            requires_contracts=_contract_requirements(row.get("requires_contracts"), f"plugins[{index}].requires_contracts"),
            requires_services=_string_tuple(row.get("requires_services"), f"plugins[{index}].requires_services"),
        ))
    return PluginPackageManifest(package=package, categories=tuple(categories), plugins=tuple(plugins), raw=dict(data))


def load_manifest(path: Path) -> PluginPackageManifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read plugin manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("plugin manifest root must be an object")
    return parse_manifest(data)


def _help_text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ManifestError("help_text must be a string")
    return value.strip()


def _string_tuple(value, field):
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ManifestError(f"{field} must be a list of non-empty strings")
    return tuple(dict.fromkeys(item.strip() for item in value))
