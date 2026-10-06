from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from cifereca.plugin_api import Dependency, PluginDescriptor

from .errors import DependencyError


@dataclass(frozen=True, slots=True)
class DependencyIssue:
    plugin_id: str
    code: str
    message: str


def dependency_satisfied(dep: Dependency, plugins: dict[str, PluginDescriptor]) -> bool:
    target = plugins.get(dep.plugin)
    if target is None:
        return False
    if dep.version and Version(target.version) not in SpecifierSet(dep.version):
        return False
    return True


def validate_dependencies(plugins: dict[str, PluginDescriptor]) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    for plugin in plugins.values():
        for dep in plugin.requires:
            target = plugins.get(dep.plugin)
            if target is None:
                issues.append(DependencyIssue(plugin.id, "dependency_missing", f"missing required plugin: {dep.plugin}"))
            elif dep.version and Version(target.version) not in SpecifierSet(dep.version):
                issues.append(DependencyIssue(
                    plugin.id, "dependency_version",
                    f"{dep.plugin} {target.version} does not satisfy {dep.version}",
                ))
    return issues


def topological_order(plugins: dict[str, PluginDescriptor], enabled: set[str] | None = None) -> list[str]:
    selected = set(plugins) if enabled is None else set(enabled)
    indegree = {plugin_id: 0 for plugin_id in selected}
    edges: dict[str, set[str]] = defaultdict(set)
    for plugin_id in selected:
        plugin = plugins[plugin_id]
        for dep in plugin.requires:
            if dep.plugin not in selected:
                continue
            if plugin_id not in edges[dep.plugin]:
                edges[dep.plugin].add(plugin_id)
                indegree[plugin_id] += 1
    queue = deque(sorted(plugin_id for plugin_id, degree in indegree.items() if degree == 0))
    out: list[str] = []
    while queue:
        current = queue.popleft()
        out.append(current)
        for child in sorted(edges.get(current, ())):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(out) != len(selected):
        cyclic = sorted(plugin_id for plugin_id, degree in indegree.items() if degree > 0)
        raise DependencyError(f"plugin dependency cycle: {', '.join(cyclic)}")
    return out
