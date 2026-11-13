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
    aliases = set(LOADERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "importlib", "importlib.util", "importlib.machinery", "builtins",
        }:
            for alias in node.names:
                if alias.name in LOADERS:
                    aliases.add(alias.asname or alias.name)
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
            arg_index = 1 if name in {"spec_from_file_location", "SourceFileLoader"} or (
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


def inventory(root: Path = ROOT) -> dict:
    root_tests = sorted((root / "tests").glob("test_*.py"))
    plugin_tests = sorted((root / "src/plugins").glob("*/tests/**/*.py"))
    sources = root_tests + [p for p in plugin_tests if p.name.startswith("test_") or p.name == "conftest.py"]
    edges: list[dict] = []
    unresolved: list[dict] = []
    errors: list[dict] = []
    root_modules = {
        "tests." + p.stem: p.relative_to(root).as_posix() for p in root_tests
    }
    adjacency: dict[str, set[str]] = {}
    plugin_direct: dict[str, set[str]] = {}
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
                    "plugin_to_root_transitive", "unresolved_dynamic_or_pytest_plugins",
                    "parse_errors"):
            value = result[key]
            print(f"{key}: {len(value) if isinstance(value, list) else value}")
    return 1 if result["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
