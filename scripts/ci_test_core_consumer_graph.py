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

ROOT = Path(__file__).resolve().parents[1]
LOADERS = frozenset({"import_module", "__import__", "spec_from_file_location", "SourceFileLoader"})


def _root_module(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) == 2 and parts[0] == "tests" and parts[1].startswith("test_"):
        return module
    return None


def _imports(source: str, *, is_root_test: bool) -> tuple[list[tuple[str, str, int]], list[int]]:
    """Return (root-test module, mechanism, line) plus unresolved loader lines."""
    tree = ast.parse(source)
    edges: set[tuple[str, str, int]] = set()
    unknown: set[int] = set()
    aliases = {name: name for name in LOADERS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "importlib", "importlib.util", "importlib.machinery", "builtins",
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
            module = _root_module(node.module or "")
            if module:
                edges.add((module, "from", node.lineno))
            elif node.module == "tests":
                for alias in node.names:
                    module = _root_module("tests." + alias.name)
                    if module:
                        edges.add((module, "from_namespace", node.lineno))
            elif is_root_test and node.level == 1 and (
                not node.module or node.module.startswith("test_")
            ):
                module = _root_module("tests." + (node.module or ""))
                if module:
                    edges.add((module, "relative", node.lineno))
                elif not node.module:
                    for alias in node.names:
                        module = _root_module("tests." + alias.name)
                        if module:
                            edges.add((module, "relative", node.lineno))
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
    root_tests = sorted((root / "tests").glob("test_*.py"))
    plugin_tests = sorted((root / "src/plugins").glob("*/tests/**/*.py"))
    # Include support modules as well as test_*.py: an imported helper can
    # itself depend on root tests, even if pytest does not collect it.
    sources = root_tests + plugin_tests
    edges: list[dict] = []
    unresolved: list[dict] = []
    errors: list[dict] = []
    root_modules = {
        "tests." + p.stem: p.relative_to(root).as_posix() for p in root_tests
    }
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
            )
        except (SyntaxError, UnicodeError, OSError) as exc:
            errors.append({"consumer": path, "reason": type(exc).__name__})
            continue
        for module, mechanism, line in found:
            target = root_modules.get(module)
            edges.append({
                "consumer": path, "owner": owner, "target_module": module,
                "target": target, "mechanism": mechanism, "line": line,
                "resolved": target is not None,
            })
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
        "schema": "cifereca.test-core-consumers/1",
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
        "unresolved_dynamic_or_pytest_plugins": sorted(
            unresolved, key=lambda e: (e["consumer"], e["line"]),
        ),
        "parse_errors": sorted(errors, key=lambda e: e["consumer"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = inventory()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        for key in ("root_test_files", "plugin_test_files", "plugin_to_root_direct",
                    "plugin_to_root_transitive", "plugin_to_plugin_helper_direct",
                    "plugin_to_root_via_helpers", "unresolved_dynamic_or_pytest_plugins",
                    "parse_errors"):
            value = result[key]
            print(f"{key}: {len(value) if isinstance(value, list) else value}")
    return 1 if result["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
