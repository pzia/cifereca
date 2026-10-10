"""Select affected plugin tests from manifests and audited dependencies."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

PACKAGE_PATH = re.compile(r"src/plugins/([A-Za-z0-9_-]+)/(.+)")
TEST_MODULE = re.compile(r"tests/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_]+\.py")
MANIFEST = "cifereca-plugin.json"
EXPLICIT_GRAPH = Path(".github/plugin-test-dependencies.json")


class GraphError(ValueError):
    pass


def _manifests(root: Path) -> dict[str, dict]:
    result = {}
    for path in sorted((root / "src/plugins").glob(f"*/{MANIFEST}")):
        name = path.parent.name
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise GraphError(f"invalid manifest: {name}") from error
        if name in result or not isinstance(manifest.get("plugins"), list):
            raise GraphError(f"invalid package: {name}")
        result[name] = manifest
    if not result:
        raise GraphError("no plugin packages")
    return result


def dependency_graph(root: Path) -> tuple[dict[str, set[str]], set[str]]:
    """Return consumer -> dependencies, including audited runtime/test links."""
    manifests = _manifests(root)
    owners: dict[str, str] = {}
    providers: dict[str, set[str]] = {}
    graph = {name: set() for name in manifests}
    for name, manifest in manifests.items():
        for plugin in manifest["plugins"]:
            plugin_id, plugin_type = plugin.get("id"), plugin.get("type")
            if not isinstance(plugin_id, str) or not isinstance(plugin_type, str):
                raise GraphError(f"invalid plugin declaration: {name}")
            if plugin_id in owners:
                raise GraphError(f"duplicate plugin id: {plugin_id}")
            owners[plugin_id] = name
            providers.setdefault(plugin_type, set()).add(name)
    for name, manifest in manifests.items():
        for plugin in manifest["plugins"]:
            for requirement in plugin.get("requires", []):
                dependency = owners.get(requirement.get("plugin"))
                if dependency is None:
                    raise GraphError(f"unknown required plugin in {name}")
                if dependency != name:
                    graph[name].add(dependency)
            for requirement in plugin.get("requires_contracts", []):
                category = requirement.get("category")
                if not isinstance(category, str):
                    raise GraphError(f"invalid contract in {name}")
                graph[name].update(provider for provider in providers.get(category, set()) if provider != name)
    try:
        explicit = json.loads((root / EXPLICIT_GRAPH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GraphError("invalid explicit dependency graph") from error
    if not isinstance(explicit, dict):
        raise GraphError("explicit dependency graph must be an object")
    audited = explicit.get("audited_product_packages")
    dependencies_by_consumer = explicit.get("dependencies")
    if not isinstance(audited, list) or not isinstance(dependencies_by_consumer, dict):
        raise GraphError("invalid explicit dependency graph schema")
    if any(package not in graph for package in audited):
        raise GraphError("unknown audited product package")
    for consumer, dependencies in dependencies_by_consumer.items():
        if consumer not in graph or not isinstance(dependencies, list):
            raise GraphError(f"unknown explicit consumer: {consumer}")
        for dependency in dependencies:
            if dependency not in graph:
                raise GraphError(f"unknown explicit dependency: {dependency}")
            if dependency != consumer:
                graph[consumer].add(dependency)
    return graph, set(audited)


def affected_packages(changed: set[str], graph: dict[str, set[str]]) -> list[str]:
    """Include transitive consumers; cycles converge through the set."""
    selected = set(changed)
    while True:
        expanded = selected | {consumer for consumer, dependencies in graph.items() if dependencies & selected}
        if expanded == selected:
            return sorted(selected)
        selected = expanded


def select(changes: list[tuple[str, str]], root: Path) -> tuple[list[str], str]:
    direct_tests: set[str] = set()
    product: set[str] = set()
    for status, path in changes:
        if path.startswith(("docs/", ".agents/")) or path == "AGENTS.md":
            continue
        match = PACKAGE_PATH.fullmatch(path)
        if status not in {"A", "M"} or not match:
            return [], "full: shared change, deletion or rename"
        package, relative = match.groups()
        package_root = root / "src/plugins" / package
        if not (package_root / MANIFEST).is_file() or not (package_root / "tests").is_dir():
            return [], "full: missing package manifest or tests"
        if relative == MANIFEST:
            return [], "full: manifest graph changed"
        if relative.startswith("tests/"):
            if not TEST_MODULE.fullmatch(relative):
                return [], "full: shared test helper changed"
            direct_tests.add(package)
        else:
            product.add(package)
    if not direct_tests and not product:
        return [], "full: no isolated plugin target"
    try:
        graph, audited = dependency_graph(root)
    except GraphError as error:
        return [], f"full: {error}"
    if not (direct_tests | product) <= graph.keys():
        return [], "full: changed package absent from graph"
    if not product <= audited:
        return [], "full: product package dependency graph not audited"
    selected = direct_tests | set(affected_packages(product, graph))
    mode = "product dependency closure" if product else "only package test modules changed"
    return [f"src/plugins/{name}" for name in sorted(selected)], f"targeted: {mode}"


def git_changes(root: Path, base: str, head: str, event: str) -> list[tuple[str, str]]:
    if event not in {"push", "pull_request"}:
        raise ValueError("manual or unknown event")
    if any(not re.fullmatch(r"[0-9a-fA-F]{40}", sha) or set(sha) == {"0"} for sha in (base, head)):
        raise ValueError("missing or zero SHA")
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)
    if event == "push" and git("rev-parse", "HEAD").decode().strip() != head:
        raise ValueError("checkout is not the requested head")
    if event == "pull_request":
        git("merge-base", "--is-ancestor", head, "HEAD")
        base = git("merge-base", base, head).decode().strip()
    else:
        git("merge-base", "--is-ancestor", base, head)
    parts = git("diff", "--no-renames", "--name-status", "-z", base, head).decode("utf-8").split("\0")
    if parts[-1:] == [""]:
        parts.pop()
    if len(parts) % 2:
        raise ValueError("invalid diff")
    return list(zip(parts[::2], parts[1::2]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--targets-file", type=Path)
    args = parser.parse_args()
    try:
        targets, reason = select(git_changes(args.root, args.base, args.head, args.event), args.root)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        targets, reason = [], "full: diff unavailable or ambiguous (" + type(error).__name__ + ")"
    print(json.dumps({"mode": "targeted" if targets else "full", "targets": targets, "reason": reason}))
    if args.targets_file:
        args.targets_file.write_text(" ".join(targets), encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write("plugin_targets=" + " ".join(targets) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
