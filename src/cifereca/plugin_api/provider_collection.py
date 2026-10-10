"""Public, data-only contract for versioned virtual provider inventories.

The plugin supplies descriptions and local provider factories; only Core may
publish their products to its private runtime registry. Parsing never performs
network I/O or calls create_provider/refresh.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

# Match the public capability syntax without a circular import through
# plugin_api.contracts (which imports this Protocol for service checks).
_CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$")


def capability_name_valid(value: str) -> bool:
    return bool(_CAPABILITY_RE.fullmatch(value))


_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
MAX_VIRTUAL_PROVIDERS = 128
MAX_CAPABILITIES = 32


class ProviderCollectionValidationError(ValueError):
    """Rejected inventory; callers must retain the previous accepted snapshot."""


@dataclass(frozen=True, slots=True)
class VirtualProviderEntry:
    plugin_id: str
    source_id: str
    capabilities: tuple[str, ...]
    source_revision: str
    catalog_generation: int
    status: str
    kind: str
    instantiation: str
    parent_plugin_id: str
    is_virtual: bool = True


@dataclass(frozen=True, slots=True)
class ProviderCollectionSnapshot:
    discovery_generation: int
    api_major: int
    parent_plugin_id: str
    children: tuple[VirtualProviderEntry, ...]


@runtime_checkable
class ProviderCollection(Protocol):
    """A parent plugin's public provider.collection/1 service.

    `snapshot` is cache-only, `refresh` requires explicit user action,
    and factories return only already-installed, local Python implementations.
    """

    def snapshot(self) -> ProviderCollectionSnapshot | Mapping[str, object]: ...

    def refresh(self, *, explicit: bool = True) -> ProviderCollectionSnapshot | Mapping[str, object]: ...

    def create_provider(self, source_id: str) -> object: ...

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]: ...


def _nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ProviderCollectionValidationError(f"{label} must be a nonnegative integer")
    return value


def _name(value: object, label: str, pattern: re.Pattern[str] = _NAME) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ProviderCollectionValidationError(f"{label} is invalid")
    return value


def _field(row: Mapping[str, object], name: str, label: str) -> object:
    if name not in row:
        raise ProviderCollectionValidationError(f"{label}.{name} is required")
    return row[name]


def _as_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderCollectionValidationError(f"{label} must be an object")
    return value


def _as_child(value: object, label: str) -> Mapping[str, object]:
    if isinstance(value, VirtualProviderEntry):
        return {
            "plugin_id": value.plugin_id, "source_id": value.source_id,
            "capabilities": value.capabilities, "source_revision": value.source_revision,
            "catalog_generation": value.catalog_generation, "status": value.status,
            "kind": value.kind, "instantiation": value.instantiation,
            "parent_plugin_id": value.parent_plugin_id, "is_virtual": value.is_virtual,
        }
    return _as_mapping(value, label)


def validate_provider_collection_snapshot(
    value: ProviderCollectionSnapshot | Mapping[str, object],
    *,
    parent_plugin_id: str,
    namespace: str = "cifericaopds",
    existing_provider_ids: Iterable[str] = (),
    previous: ProviderCollectionSnapshot | None = None,
    max_children: int = MAX_VIRTUAL_PROVIDERS,
) -> ProviderCollectionSnapshot:
    """Validate the *whole* inventory before allowing any Core publication.

    Reject namespace escapes, duplicate/colliding IDs, malformed schema,
    stale revisions and inconsistent replay; no partial children are returned.
    Parent/child provider objects are never instantiated by this function.
    """
    parent = _name(parent_plugin_id, "parent_plugin_id")
    prefix = _name(namespace, "namespace", _SOURCE_ID)
    if not isinstance(max_children, int) or isinstance(max_children, bool) or not 1 <= max_children <= MAX_VIRTUAL_PROVIDERS:
        raise ProviderCollectionValidationError("invalid max_children")
    if isinstance(value, ProviderCollectionSnapshot):
        root: Mapping[str, object] = {
            "discovery_generation": value.discovery_generation,
            "api_major": value.api_major,
            "parent_plugin_id": value.parent_plugin_id,
            "children": value.children,
        }
    else:
        root = _as_mapping(value, "snapshot")
    major = _nonnegative_int(_field(root, "api_major", "snapshot"), "api_major")
    if major != 1:
        raise ProviderCollectionValidationError("unsupported api_major")
    generation = _nonnegative_int(
        _field(root, "discovery_generation", "snapshot"), "discovery_generation"
    )
    if _field(root, "parent_plugin_id", "snapshot") != parent:
        raise ProviderCollectionValidationError("parent_plugin_id mismatch")
    raw_children = _field(root, "children", "snapshot")
    if not isinstance(raw_children, (list, tuple)):
        raise ProviderCollectionValidationError("children must be a list")
    if len(raw_children) > max_children:
        raise ProviderCollectionValidationError("virtual provider limit exceeded")
    old = {c.plugin_id: c for c in previous.children} if previous else {}
    if previous is not None:
        if previous.parent_plugin_id != parent or previous.api_major != major:
            raise ProviderCollectionValidationError("incompatible previous snapshot")
        if generation < previous.discovery_generation:
            raise ProviderCollectionValidationError("stale discovery generation")
    existing = {str(pid).strip().lower() for pid in existing_provider_ids}
    seen: set[str] = set()
    children: list[VirtualProviderEntry] = []
    for i, child in enumerate(raw_children):
        label = f"children[{i}]"
        row = _as_child(child, label)
        source = _name(_field(row, "source_id", label), f"{label}.source_id", _SOURCE_ID)
        plugin_id = f"{prefix}.{source}"
        if _field(row, "plugin_id", label) != plugin_id:
            raise ProviderCollectionValidationError(f"{label}: namespace mismatch")
        if plugin_id in seen:
            raise ProviderCollectionValidationError(f"{label}: duplicate provider")
        if plugin_id in existing and plugin_id not in old:
            raise ProviderCollectionValidationError(f"{label}: existing provider collision")
        seen.add(plugin_id)
        if _field(row, "parent_plugin_id", label) != parent:
            raise ProviderCollectionValidationError(f"{label}: parent mismatch")
        if _field(row, "is_virtual", label) is not True:
            raise ProviderCollectionValidationError(f"{label}: not virtual")
        if _field(row, "instantiation", label) != "singleton_public":
            raise ProviderCollectionValidationError(f"{label}: unsupported instantiation")
        caps = _field(row, "capabilities", label)
        if not isinstance(caps, (list, tuple)) or len(caps) > MAX_CAPABILITIES:
            raise ProviderCollectionValidationError(f"{label}: invalid capabilities")
        if any(not isinstance(cap, str) or not capability_name_valid(cap) for cap in caps):
            raise ProviderCollectionValidationError(f"{label}: invalid capability")
        if len(set(caps)) != len(caps):
            raise ProviderCollectionValidationError(f"{label}: duplicate capability")
        revision = _field(row, "source_revision", label)
        if not isinstance(revision, str) or not revision.strip() or len(revision) > 128:
            raise ProviderCollectionValidationError(f"{label}: invalid source_revision")
        catalog = _nonnegative_int(
            _field(row, "catalog_generation", label), f"{label}.catalog_generation"
        )
        prior = old.get(plugin_id)
        if prior and catalog < prior.catalog_generation:
            raise ProviderCollectionValidationError(f"{label}: stale catalog generation")
        status = _name(_field(row, "status", label), f"{label}.status")
        kind = _name(_field(row, "kind", label), f"{label}.kind")
        children.append(VirtualProviderEntry(
            plugin_id=plugin_id, source_id=source, capabilities=tuple(caps),
            source_revision=revision, catalog_generation=catalog,
            status=status, kind=kind, instantiation="singleton_public",
            parent_plugin_id=parent,
        ))
    result = ProviderCollectionSnapshot(
        discovery_generation=generation, api_major=major,
        parent_plugin_id=parent, children=tuple(children),
    )
    if previous and generation == previous.discovery_generation and result != previous:
        raise ProviderCollectionValidationError("changed snapshot at same generation")
    return result


__all__ = [
    "ProviderCollection", "ProviderCollectionSnapshot", "VirtualProviderEntry",
    "ProviderCollectionValidationError", "validate_provider_collection_snapshot",
    "MAX_VIRTUAL_PROVIDERS", "MAX_CAPABILITIES",
]
