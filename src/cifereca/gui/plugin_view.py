from __future__ import annotations

from collections.abc import Iterable


PLUGIN_VIEW_FILTERS = (
    ("all", "Tous"),
    ("active", "Actifs"),
    ("disabled", "Désactivés"),
    ("attention", "À examiner"),
)

_ATTENTION_STATES = frozenset({"blocked", "error", "incompatible", "degraded"})

# Roles are presentation-only; category contracts stay managed by PluginManager.
PLUGIN_ROLE_FILTERS = (
    ("all", "Tous les rôles"),
    ("feature", "Fonctionnalités"),
    ("source", "Sources"),
    ("service", "Services"),
    ("ui", "Interface"),
    ("technical", "Techniques"),
)
PLUGIN_ROLE_LABELS = dict(PLUGIN_ROLE_FILTERS)

_SOURCE_TYPES = frozenset({"provider.library", "storage.target"})
_SERVICE_TYPES = frozenset({"catalog.service", "catalog.enrichment", "book.detail.enrichment"})
_TECHNICAL_TYPES = frozenset({
    "transport", "drm.handler", "runtime.adapter", "device.profile",
    "maintenance.tool", "diagnostics.check",
})


def plugin_role(descriptor: object) -> str:
    """Map public category IDs to a stable, display-only inventory filter."""
    category = str(getattr(descriptor, "type", "") or "").strip()
    if category.startswith("ui."):
        return "ui"
    if (category in _SOURCE_TYPES or category.startswith(("provider.", "source."))
            or category.endswith(".source") or ".source." in category):
        return "source"
    if category in _SERVICE_TYPES or category.endswith(".enrichment"):
        return "service"
    if category in _TECHNICAL_TYPES or category.startswith("runtime."):
        return "technical"
    return "feature"


def plugin_dependents(
    plugin_id: str, statuses: Iterable[object], *, transitive: bool = False,
) -> tuple[object, ...]:
    """Dependents through declared required plugin IDs only, never guesses."""
    rows = tuple(statuses)
    found: dict[str, object] = {}
    visited = {plugin_id}
    pending = [plugin_id]
    while pending:
        current = pending.pop(0)
        for row in rows:
            descriptor = getattr(row, "descriptor", None)
            identifier = str(getattr(descriptor, "id", "") or "")
            if not identifier or identifier in visited:
                continue
            if not any(
                getattr(dep, "plugin", None) == current
                for dep in getattr(descriptor, "requires", ())
            ):
                continue
            visited.add(identifier)
            found[identifier] = row
            if transitive:
                pending.append(identifier)
        if not transitive:
            break
    return tuple(found[key] for key in sorted(found))


def plugin_relationships(status: object, statuses: Iterable[object]) -> tuple[str, ...]:
    """Readable relationships backed exclusively by manifest declarations."""
    descriptor = getattr(status, "descriptor", None)
    if descriptor is None:
        return ()
    rows = tuple(statuses)
    by_id = {
        row.descriptor.id: row.descriptor
        for row in rows if getattr(row, "descriptor", None) is not None
    }

    def names(items: Iterable[str]) -> str:
        ids = sorted(set(str(value) for value in items if value))
        return ", ".join(
            str(getattr(by_id.get(identifier), "display_name", "") or identifier)
            for identifier in ids[:4]
        ) + (f" (+{len(ids) - 4})" if len(ids) > 4 else "")

    requires = tuple(getattr(descriptor, "requires", ()) or ())
    recommends = tuple(getattr(descriptor, "recommends", ()) or ())
    required_by = plugin_dependents(descriptor.id, rows)
    recommended_by = [
        row for row in rows
        if any(
            getattr(dep, "plugin", None) == descriptor.id
            for dep in (getattr(row.descriptor, "recommends", ()) or ())
        )
    ]
    lines = []
    if requires:
        lines.append("Requiert : " + names(dep.plugin for dep in requires))
    if required_by:
        lines.append("Requis par : " + names(row.descriptor.id for row in required_by))
    if recommends:
        lines.append("Recommande : " + names(dep.plugin for dep in recommends))
    if recommended_by:
        lines.append("Recommandé pour : " + names(row.descriptor.id for row in recommended_by))
    contracts = tuple(getattr(descriptor, "requires_contracts", ()) or ())
    if contracts:
        values = sorted({
            str(contract.category)
            for contract in contracts if getattr(contract, "category", None)
        })
        lines.append("Contrats utilisés : " + names(values))
    return tuple(lines)


def plugin_summary_counts(statuses: Iterable[object]) -> dict[str, int]:
    rows = list(statuses)
    return {
        "total": len(rows),
        "activated": sum(bool(getattr(row, "enabled", False)) for row in rows),
        "ready": sum(
            bool(getattr(row, "enabled", False))
            and str(getattr(row, "state", "") or "") == "ready"
            for row in rows
        ),
        "attention": sum(
            str(getattr(row, "state", "") or "") in _ATTENTION_STATES
            for row in rows
        ),
    }


def _text(value: object) -> str:
    return str(value or "").casefold()


def package_matches_query(package: dict[str, object], query: str) -> bool:
    needle = str(query or "").strip().casefold()
    if not needle:
        return True
    haystack = " ".join(
        _text(package.get(key))
        for key in ("name", "package_id", "publisher")
    )
    return needle in haystack


def _status_matches_state(status: object, state_filter: str) -> bool:
    if state_filter == "all":
        return True
    enabled = bool(getattr(status, "enabled", False))
    state = str(getattr(status, "state", "") or "")
    if state_filter == "active":
        return enabled
    if state_filter == "disabled":
        return not enabled
    if state_filter == "attention":
        return state in _ATTENTION_STATES
    return True


def filter_package_plugins(
    package: dict[str, object],
    statuses: Iterable[object],
    *,
    query: str = "",
    state_filter: str = "all",
    role_filter: str = "all",
) -> list[object]:
    needle = str(query or "").strip().casefold()
    package_match = package_matches_query(package, needle)
    out: list[object] = []
    for status in statuses:
        if not _status_matches_state(status, state_filter):
            continue
        if role_filter != "all" and plugin_role(getattr(status, "descriptor", None)) != role_filter:
            continue
        if package_match:
            out.append(status)
            continue
        descriptor = getattr(status, "descriptor", None)
        if descriptor is None:
            continue
        haystack = " ".join(
            _text(value)
            for value in (
                getattr(descriptor, "display_name", ""),
                getattr(descriptor, "description", ""),
                getattr(descriptor, "id", ""),
                getattr(descriptor, "package_id", ""),
            )
        )
        if needle in haystack:
            out.append(status)
    return out


def plugin_extension_packages(statuses: Iterable[object]) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Return child package -> (parent package, declared matching contracts).

    Only public requires_contracts declarations establish a relation: package
    names, plugin IDs, and a shared prefix cannot prove parentage. A package
    used by several independent parents stays standalone rather than picking
    an arbitrary parent. There is deliberately no inference from UI labels.
    """
    rows = tuple(statuses)
    by_package: dict[str, list[object]] = {}
    for row in rows:
        descriptor = getattr(row, "descriptor", None)
        package_id = str(getattr(descriptor, "package_id", "") or "")
        if package_id:
            by_package.setdefault(package_id, []).append(descriptor)

    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for child_id, members in sorted(by_package.items()):
        # Never tuck an independently usable feature or UI page beneath
        # another package merely because it also exports a source.
        if not members or any(plugin_role(member) not in {"source", "service", "technical"} for member in members):
            continue
        categories = {str(getattr(member, "type", "") or "") for member in members}
        parents: dict[str, set[str]] = {}
        for parent_id, providers in by_package.items():
            if child_id == parent_id:
                continue
            for parent in providers:
                # Only a feature/UI consuming a declared category makes
                # that category an extension of the parent package.
                if plugin_role(parent) not in {"feature", "ui"}:
                    continue
                for requirement in getattr(parent, "requires_contracts", ()) or ():
                    category = str(getattr(requirement, "category", "") or "")
                    if category in categories:
                        parents.setdefault(parent_id, set()).add(category)
        if len(parents) == 1:
            parent_id, contracts = next(iter(parents.items()))
            out[child_id] = (parent_id, tuple(sorted(contracts)))
    return out
