#!/usr/bin/env python3
"""Reproducible static graph of plugin-test dependencies on root tests (#5376).

Read-only: never imports or executes a test/provider. An unknown dynamic import
is explicitly unresolved, never counted as a proven dependency.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

try:  # support module and direct script invocation
    from scripts.ci_test_dependency_guard import has_test_consumers
except ModuleNotFoundError:
    from ci_test_dependency_guard import has_test_consumers

ROOT = Path(__file__).resolve().parents[1]
LOADERS = frozenset({
    "import_module", "__import__", "spec_from_file_location", "SourceFileLoader",
    "importorskip", "run_module", "run_path",
})


def _root_module(module: str) -> str | None:
    parts = module.split(".")
    if (len(parts) >= 2 and parts[0] == "tests"
            and all(parts) and parts[-1].startswith("test_")):
        return module
    return None


def _imports(
    source: str, *, is_root_test: bool, consumer_module: str | None = None,
) -> tuple[list[tuple[str, str, int]], list[int]]:
    """Return (root-test module, mechanism, line) plus unresolved loader lines."""
    tree = ast.parse(source)
    edges: set[tuple[str, str, int]] = set()
    unknown: set[int] = set()
    aliases = {name: name for name in LOADERS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "importlib", "importlib.util", "importlib.machinery", "builtins",
            "pytest", "runpy",
        }:
            for alias in node.names:
                if alias.name in LOADERS:
                    aliases[alias.asname or alias.name] = alias.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = _root_module(alias.name)
                if module:
                    edges.add((module, "import", node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Only imports in *root* test packages can refer to a
                # relative Core fixture. A plugin's ".test_x" is local to
                # that plugin, even if a root test has the same name.
                if not is_root_test:
                    continue
                package = (consumer_module or "tests._unknown").split(".")[:-1]
                if node.level > len(package):
                    unknown.add(node.lineno)
                    continue
                prefix = ".".join(package[:len(package) - node.level + 1])
                candidate = prefix + ("." + node.module if node.module else "")
                module = _root_module(candidate)
                if module:
                    edges.add((module, "relative", node.lineno))
                else:
                    for alias in node.names:
                        module = _root_module(candidate + "." + alias.name)
                        if module:
                            edges.add((module, "relative", node.lineno))
                continue
            module = _root_module(node.module or "")
            if module:
                edges.add((module, "from", node.lineno))
            elif node.module and (
                node.module == "tests" or node.module.startswith("tests.")
            ):
                for alias in node.names:
                    module = _root_module(node.module + "." + alias.name)
                    if module:
                        edges.add((module, "from_namespace", node.lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
                value = node.value
                elements = value.elts if isinstance(value, (ast.Tuple, ast.List, ast.Set)) else [value]
                for entry in elements:
                    if isinstance(entry, ast.Constant) and type(entry.value) is str:
                        module = _root_module(entry.value)
                        if module:
                            edges.add((module, "pytest_plugins", node.lineno))
                    else:
                        unknown.add(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else ""
            )
            if name not in aliases:
                continue
            # Import module is the first argument; file-based loaders carry
            # the filesystem module location in their second argument.
            arg_index = 1 if aliases[name] in {"spec_from_file_location", "SourceFileLoader"} or (
                isinstance(func, ast.Attribute) and func.attr in {
                    "spec_from_file_location", "SourceFileLoader",
                }
            ) else 0
            if len(node.args) <= arg_index:
                unknown.add(node.lineno)
                continue
            value = node.args[arg_index]
            if isinstance(value, ast.Constant) and type(value.value) is str:
                module = _root_module(value.value.replace("/", ".").removesuffix(".py"))
                if module:
                    edges.add((module, "dynamic_literal", node.lineno))
            else:
                unknown.add(node.lineno)
    return sorted(edges), sorted(unknown)



def _local_plugin_helper_imports(
    source: Path, root: Path, known: set[str],
) -> list[tuple[str, int]]:
    """Resolve only exact relative imports within this plugin's tests tree.

    An absolute `tests.helpers` is ambiguous with root tests; it must not
    silently become a dependency on the local plugin directory.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tests_root = root / "src/plugins" / source.relative_to(root).parts[2] / "tests"
    links: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level < 1:
            continue
        base = source.parent
        for _ in range(node.level - 1):
            base = base.parent
        try:
            base.relative_to(tests_root)
        except ValueError:
            continue
        module = (node.module or "").split(".") if node.module else []
        candidates = [base.joinpath(*module)] if module else []
        # `from . import helpers` loads a sibling; `from .helpers import
        # fixture` loads helpers, and can also load a helpers.fixture module.
        candidates.extend(base.joinpath(*module, alias.name)
                          for alias in node.names if alias.name != "*")
        for candidate in candidates:
            for match in (candidate.with_suffix(".py"), candidate / "__init__.py"):
                rel = match.relative_to(root).as_posix()
                if rel in known:
                    links.add((rel, node.lineno))
    return sorted(links)

def inventory(root: Path = ROOT) -> dict:
    root_tests = sorted((root / "tests").glob("**/test_*.py"))
    plugin_tests = sorted((root / "src/plugins").glob("*/tests/**/*.py"))
    # Include support modules as well as test_*.py: an imported helper can
    # itself depend on root tests, even if pytest does not collect it.
    sources = root_tests + plugin_tests
    edges: list[dict] = []
    unresolved: list[dict] = []
    local_edges: list[dict] = []
    ambiguous_edges: list[dict] = []
    missing_edges: list[dict] = []
    errors: list[dict] = []
    root_modules = {
        ".".join(p.relative_to(root).with_suffix("").parts):
        p.relative_to(root).as_posix() for p in root_tests
    }
    plugin_modules = {}
    for source in plugin_tests:
        path = source.relative_to(root)
        if len(path.parts) >= 5 and path.name.startswith("test_"):
            module_parts = source.relative_to(
                root / "src/plugins" / path.parts[2] / "tests"
            ).with_suffix("").parts
            plugin_modules[(path.parts[2], "tests." + ".".join(module_parts))] = path.as_posix()
    adjacency: dict[str, set[str]] = {}
    plugin_direct: dict[str, set[str]] = {}
    plugin_helpers: dict[str, set[str]] = {}
    plugin_helper_edges: list[dict] = []
    plugin_known = {path.relative_to(root).as_posix() for path in plugin_tests}
    for source in sources:
        path = source.relative_to(root).as_posix()
        parts = source.relative_to(root).parts
        owner = "root_tests" if parts[0] == "tests" else "plugin:" + parts[2]
        try:
            found, unknown = _imports(
                source.read_text(encoding="utf-8"), is_root_test=owner == "root_tests",
                consumer_module=path.removesuffix(".py").replace("/", ".")
                if owner == "root_tests" else None,
            )
        except (SyntaxError, UnicodeError, OSError) as exc:
            errors.append({"consumer": path, "reason": type(exc).__name__})
            continue
        for module, mechanism, line in found:
            target = root_modules.get(module)
            edge = {
                "consumer": path, "owner": owner, "target_module": module,
                "target": target, "mechanism": mechanism, "line": line,
                "resolved": target is not None,
            }
            if owner != "root_tests":
                local_target = plugin_modules.get((parts[2], module))
                if local_target is not None:
                    # Namespace selection depends on the test runner if both
                    # roots provide the module. Do not infer an import edge.
                    if target is not None:
                        ambiguous_edges.append({**edge, "local_target": local_target})
                    else:
                        local_edges.append({**edge, "target": local_target, "resolved": True})
                    continue
                if target is None:
                    missing_edges.append(edge)
                    continue
            edges.append(edge)
            if target is not None:
                if owner == "root_tests":
                    adjacency.setdefault(path, set()).add(target)
                else:
                    plugin_direct.setdefault(path, set()).add(target)
        for line in unknown:
            unresolved.append({"consumer": path, "owner": owner, "line": line})
        if owner.startswith("plugin:"):
            try:
                links = _local_plugin_helper_imports(source, root, plugin_known)
            except (SyntaxError, UnicodeError, OSError) as exc:
                errors.append({"consumer": path, "reason": type(exc).__name__})
                continue
            for target, line in links:
                plugin_helpers.setdefault(path, set()).add(target)
                plugin_helper_edges.append({
                    "consumer": path, "owner": owner, "target": target,
                    "mechanism": "relative_plugin_helper", "line": line,
                })
    transitive: list[dict] = []
    for consumer, starts in sorted(plugin_direct.items()):
        visited = set(starts)
        pending = sorted(starts)
        while pending:
            current = pending.pop()
            for target in sorted(adjacency.get(current, ())):
                if target not in visited:
                    visited.add(target)
                    pending.append(target)
        for target in sorted(visited - starts):
            transitive.append({"consumer": consumer, "target": target})
    via_helpers: list[dict] = []
    for consumer in sorted(plugin_known):
        visited = {consumer}
        pending = [consumer]
        while pending:
            current = pending.pop()
            for helper in sorted(plugin_helpers.get(current, ())):
                if helper not in visited:
                    visited.add(helper)
                    pending.append(helper)
        inherited = set()
        for helper in visited - {consumer}:
            inherited.update(plugin_direct.get(helper, ()))
        inherited.difference_update(plugin_direct.get(consumer, ()))
        # An intermediate root-test fixture may itself import another root
        # fixture. Reuse the same directed root-test graph as direct consumers.
        queue = sorted(inherited)
        while queue:
            current = queue.pop()
            for target in sorted(adjacency.get(current, ())):
                if target not in inherited and target not in plugin_direct.get(consumer, ()):
                    inherited.add(target)
                    queue.append(target)
        for target in sorted(inherited):
            via_helpers.append({"consumer": consumer, "target": target})
    return {
        "schema": "cifereca.test-core-consumers/2",
        "root_test_files": len(root_tests),
        "plugin_test_files": len(sources) - len(root_tests),
        "plugin_to_root_direct": sorted(
            (e for e in edges if e["owner"].startswith("plugin:")),
            key=lambda e: (e["consumer"], e["target_module"], e["line"]),
        ),
        "root_to_root_direct": sorted(
            (e for e in edges if e["owner"] == "root_tests"),
            key=lambda e: (e["consumer"], e["target_module"], e["line"]),
        ),
        "plugin_to_root_transitive": transitive,
        "plugin_to_plugin_helper_direct": sorted(
            plugin_helper_edges,
            key=lambda edge: (edge["consumer"], edge["target"], edge["line"]),
        ),
        "plugin_to_root_via_helpers": via_helpers,
        "plugin_local_imports": local_edges,
        "plugin_ambiguous_imports": ambiguous_edges,
        "plugin_missing_imports": missing_edges,
        "unresolved_dynamic_or_pytest_plugins": sorted(
            unresolved, key=lambda e: (e["consumer"], e["line"]),
        ),
        "parse_errors": sorted(errors, key=lambda e: e["consumer"]),
    }


def plugin_replay_required(changed: list[str], root: Path = ROOT) -> bool:
    """Select plugin replays for changed root-test fixtures and consumers.

    Follow static direct and transitive edges. Unknown loader provenance or
    parsing errors must never make the affected plugin suites silently green.
    """
    tests = {
        path.strip() for path in changed
        if path.strip().startswith("tests/")
        and Path(path.strip()).name.startswith("test_")
        and path.strip().endswith(".py")
    }
    if not tests:
        return False
    modules = {path.removesuffix(".py").replace("/", ".") for path in tests}
    # Non-root test packages may not have a stable import namespace in all
    # pytest modes. Until exact namespace proof exists, replay plugins for
    # changes to nested Core tests rather than risk a false green.
    if any(Path(path).parent != Path("tests") for path in tests):
        return True
    graph = inventory(root)
    if graph["parse_errors"]:
        return True
    edges = (
        graph["plugin_to_root_direct"]
        + graph["plugin_to_root_transitive"]
        + graph["plugin_to_root_via_helpers"]
    )
    if any(edge.get("target") in tests for edge in edges):
        return True
    if any(
        edge["target_module"] in modules
        for key in ("plugin_ambiguous_imports", "plugin_missing_imports")
        for edge in graph[key]
    ):
        return True
    reachable = {edge["target"] for edge in edges if edge.get("target") is not None}
    if any(
        edge["owner"] == "root_tests" and edge["consumer"] in reachable
        for edge in graph["unresolved_dynamic_or_pytest_plugins"]
    ):
        return True
    try:
        return has_test_consumers(
            root, sorted(tests),
            consumer_globs=("src/plugins/*/tests/**/*.py",),
        )
    except (OSError, SyntaxError, UnicodeError, ValueError):
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--plugin-replay-changed", type=Path,
                        help="Print full/none when changed root tests affect plugins")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.plugin_replay_changed is not None:
        try:
            changed = args.plugin_replay_changed.read_text(encoding="utf-8").splitlines()
            full = plugin_replay_required(changed, args.root)
        except (OSError, SyntaxError, UnicodeError, ValueError):
            full = True
        print("full" if full else "none")
        return 0
    result = inventory(args.root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        for key in ("root_test_files", "plugin_test_files", "plugin_to_root_direct",
                    "plugin_to_root_transitive", "plugin_to_plugin_helper_direct",
                    "plugin_to_root_via_helpers",
                    "plugin_local_imports",
                    "plugin_ambiguous_imports", "plugin_missing_imports",
                    "unresolved_dynamic_or_pytest_plugins",
                    "parse_errors"):
            value = result[key]
            print(f"{key}: {len(value) if isinstance(value, list) else value}")
    return 1 if result["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
