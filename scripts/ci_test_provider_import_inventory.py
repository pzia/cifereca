#!/usr/bin/env python3
"""Read-only #5376 inventory: root tests importing provider plugin modules.

This measures *direct static dependencies*, not whether an import is wrong.
Module ownership derives from actual src/plugins/provider-* Python files,
not from substrings in test names or assumed provider names.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOADERS = frozenset({"import_module", "importorskip", "__import__", "run_module"})


def modules_by_provider(root: Path) -> dict[str, list[dict[str, str]]]:
    mapping: dict[str, list[dict[str, str]]] = {}
    for source in sorted((root / "src/plugins").glob("provider-*/*.py")):
        if source.name == "__init__.py" or not source.is_file() or source.is_symlink():
            continue
        mapping.setdefault(source.stem, []).append({
            "provider": source.parent.name,
            "source": source.relative_to(root).as_posix(),
        })
    return {key: sorted(value, key=lambda row: (row["provider"], row["source"]))
            for key, value in sorted(mapping.items())}


def _module_match(imported: str, known: dict[str, list]) -> str | None:
    """An import of module.subobject belongs to the longest known prefix."""
    parts = imported.split(".")
    for length in range(len(parts), 0, -1):
        prefix = ".".join(parts[:length])
        if prefix in known:
            return prefix
    return None


def inspect_source(path: str, content: str, modules: dict[str, list]) -> tuple[list[dict[str, Any]], list[int]]:
    parsed = ast.parse(content, filename=path)
    direct: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    unresolved: set[int] = set()
    loader_aliases = set(LOADERS)
    for node in ast.walk(parsed):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "importlib", "pytest", "builtins", "runpy",
        }:
            for alias in node.names:
                if alias.name in LOADERS:
                    loader_aliases.add(alias.asname or alias.name)

    def record(imported: str, mechanism: str, line: int, symbol: str | None = None) -> None:
        target = _module_match(imported, modules)
        if not target:
            return
        owners = modules[target]
        entry = {
            "test": path, "line": line, "module": target,
            "imported": imported, "mechanism": mechanism,
            "provider": owners[0]["provider"] if len(owners) == 1 else None,
            "provider_source": owners[0]["source"] if len(owners) == 1 else None,
            "owner_ambiguous": len(owners) != 1,
            "imported_private_symbol": bool(symbol and symbol.startswith("_")),
        }
        direct[(line, target, imported, mechanism)] = entry

    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            for alias in node.names:
                record(alias.name, "import", node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # The imported module may itself be an explicit module symbol:
            # from provider_pkg import module, or from provider_module import helper.
            record(node.module, "from", node.lineno)
            for alias in node.names:
                record(node.module + "." + alias.name, "from", node.lineno, alias.name)
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else ""
            )
            if name not in loader_aliases:
                continue
            # Qualified functions are recognised only from importlib/pytest/
            # runpy/builtins. A third-party .import_module is not evidence.
            if isinstance(fn, ast.Attribute) and (
                not isinstance(fn.value, ast.Name)
                or fn.value.id not in {"importlib", "pytest", "runpy", "builtins"}
            ):
                continue
            if not node.args:
                unresolved.add(node.lineno)
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and type(first.value) is str:
                record(first.value, "dynamic_literal", node.lineno)
            else:
                # It may import a provider, but cannot be attested as a direct
                # edge without evaluating user-controlled test code.
                unresolved.add(node.lineno)
    return (
        sorted(direct.values(), key=lambda row: (
            row["test"], row["line"], row["module"], row["imported"],
            row["mechanism"],
        )),
        sorted(unresolved),
    )


def inventory(root: Path = ROOT) -> dict[str, Any]:
    owners = modules_by_provider(root)
    paths = sorted((root / "tests").glob("**/test_*.py"))
    edges: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for candidate in paths:
        relative = candidate.relative_to(root).as_posix()
        if not candidate.is_file() or candidate.is_symlink():
            errors.append({"test": relative, "reason": "not_regular_file"})
            continue
        try:
            found, unresolved = inspect_source(
                relative, candidate.read_text(encoding="utf-8"), owners,
            )
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({"test": relative, "reason": type(exc).__name__})
            continue
        edges.extend(found)
        unknown.extend({"test": relative, "line": line}
                       for line in unresolved)
    tests_with_imports = {row["test"] for row in edges}
    providers = Counter(row["provider"] or "ambiguous" for row in edges)
    ambiguous = [row for row in edges if row["owner_ambiguous"]]
    return {
        "schema": "cifereca.root-test-provider-imports/1",
        "root_test_modules_scanned": len(paths),
        "provider_modules_indexed": len(owners),
        "root_test_modules_with_direct_provider_imports": len(tests_with_imports),
        "direct_import_edges": len(edges),
        "private_symbol_edges": sum(row["imported_private_symbol"] for row in edges),
        "ambiguous_owner_edges": len(ambiguous),
        "by_provider": dict(sorted(providers.items())),
        "edges": sorted(edges, key=lambda row: (
            row["test"], row["line"], row["module"], row["imported"],
        )),
        "unresolved_dynamic_import_calls": sorted(
            unknown, key=lambda row: (row["test"], row["line"])
        ),
        "parse_or_io_errors": sorted(errors, key=lambda row: row["test"]),
        "note": (
            "Only imports proven by AST against the provider plugin filesystem "
            "are counted; private names are review candidates, not deletions. "
            "Unresolved dynamic calls do not count as proven edges. "
            "One fixture import transitively depending on a provider is "
            "not attributed here; inspect the separate root-test consumer graph."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.root.is_dir() or args.root.is_symlink():
        parser.error("root must be an existing non-symlink directory")
    report = inventory(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        for name in (
            "root_test_modules_scanned", "provider_modules_indexed",
            "root_test_modules_with_direct_provider_imports",
            "direct_import_edges", "private_symbol_edges",
            "ambiguous_owner_edges",
        ):
            print(f"{name}: {report[name]}")
        print("unresolved_dynamic_import_calls:",
              len(report["unresolved_dynamic_import_calls"]))
        print("parse_or_io_errors:", len(report["parse_or_io_errors"]))
        print("by_provider:", json.dumps(report["by_provider"], sort_keys=True))
    return 1 if report["parse_or_io_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
