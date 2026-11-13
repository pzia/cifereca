"""#5048: function-scoped, read-only AST worklist for provider coupling.

Deduplicates physical signal lines even when aliases, kinds and providers
overlap. Function and responsibility grouping is static and informative only:
a signal is NOT proof of a live business decision or of removable code.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import json
from pathlib import Path

from scripts.ci_provider_core_coupling_inventory import (
    _dedicated_provider, inspect_source,
)
from scripts.ci_provider_core_coupling_report import (
    _installed_provider_aliases, _SIGNAL_KINDS,
)


def canonical_provider(alias: str) -> str:
    value = alias.casefold().replace("_", "-")
    for provider in ("ebooksgratuits", "gallica", "numilog", "kobo", "paris", "bnf"):
        if provider in value:
            return provider
    return value


def responsibility(path: str) -> str:
    if path.endswith("/provider_sessions.py"):
        return "session_and_account"
    if path.endswith("/sync_coordination.py"):
        return "synchronization"
    if path.endswith("/search_orchestration.py"):
        return "search"
    if path.endswith("/storage.py"):
        return "sqlite_provenance"
    if path.endswith("/services.py"):
        return "core_facade"
    if "/gui/" in path:
        return "core_ui"
    if "/plugin_api/" in path:
        return "contract_or_compat"
    return "other_core"


def function_ranges(tree: ast.Module):
    result = []
    def collect(node, ancestors):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                path = (*ancestors, child.name)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result.append((child.lineno, child.end_lineno or child.lineno, ".".join(path)))
                collect(child, path)
            else:
                collect(child, ancestors)
    collect(tree, ())
    return result


def scope_for_line(ranges, line: int) -> str:
    matched = [
        (end - begin, -begin, name)
        for begin, end, name in ranges if begin <= line <= end
    ]
    return min(matched)[2] if matched else "<module>"


def inventory(root: Path) -> dict[str, object]:
    root = root.resolve()
    aliases = _installed_provider_aliases(root)
    entries = []
    parse_errors = []
    for path in sorted((root / "src" / "cifereca").rglob("*.py")):
        relative = path.relative_to(root)
        if _dedicated_provider(relative) is not None:
            continue
        rel = relative.as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
            found = inspect_source(source, providers=aliases)
        except (SyntaxError, UnicodeError) as exc:
            parse_errors.append({"path": rel, "error": type(exc).__name__})
            continue
        lines: dict[int, dict[str, set[str]]] = defaultdict(
            lambda: {"providers": set(), "kinds": set()}
        )
        for kind in _SIGNAL_KINDS:
            for signal in found[kind]:
                line = int(signal["line"])
                lines[line]["kinds"].add(kind)
                lines[line]["providers"].update(
                    canonical_provider(str(p)) for p in signal["providers"]
                )
        ranges = function_ranges(tree)
        for line, row in sorted(lines.items()):
            entries.append({
                "path": rel,
                "line": line,
                "function": scope_for_line(ranges, line),
                "responsibility": responsibility(rel),
                "providers": sorted(row["providers"]),
                "kinds": sorted(row["kinds"]),
                "review": "unclassified",
            })
    files = Counter(e["path"] for e in entries)
    functions = defaultdict(list)
    providers = Counter()
    responsibilities = Counter()
    for item in entries:
        functions[(item["path"], item["function"])].append(item)
        providers.update(item["providers"])
        responsibilities[item["responsibility"]] += 1
    ranked = sorted(
        ({
            "path": path, "function": function, "unique_lines": len(items),
            "providers": sorted({p for item in items for p in item["providers"]}),
            "kinds": dict(sorted(Counter(
                kind for item in items for kind in item["kinds"]
            ).items())),
            "responsibility": responsibility(path),
            "review": "unclassified",
        } for (path, function), items in functions.items()),
        key=lambda row: (-row["unique_lines"], row["path"], row["function"]),
    )
    return {
        "metric": "core.provider.ast-by-function/1",
        "unique_lines": len(entries),
        "overlapping_occurrences_by_provider": dict(sorted(providers.items())),
        "unique_lines_by_responsibility": dict(sorted(responsibilities.items())),
        "files": len(files),
        "functions": len(functions),
        "top_functions": ranked,
        "entries": entries,
        "parse_errors": parse_errors,
        "interpretation": (
            "Unique lines across all providers; provider counts overlap. "
            "Function assignment is AST-only; classification must be reviewed "
            "against real business authority, never inferred from names alone."
        ),
    }


def compare_counts(baseline: dict, current: dict) -> dict:
    """Compare counts without treating line shifts as added or removed signals."""
    if baseline.get("metric") != current["metric"] or baseline.get("parse_errors"):
        raise ValueError("incompatible_or_incomplete_ast_baseline")

    def counts(report):
        return Counter((entry["path"], entry["function"]) for entry in report["entries"])

    before, after = counts(baseline), counts(current)
    changes = [
        {
            "path": path,
            "function": function,
            "responsibility": responsibility(path),
            "before": before[path, function],
            "after": after[path, function],
            "delta": after[path, function] - before[path, function],
        }
        for path, function in sorted(before.keys() | after.keys())
        if before[path, function] != after[path, function]
    ]
    return {
        "before": baseline["unique_lines"],
        "after": current["unique_lines"],
        "delta": current["unique_lines"] - baseline["unique_lines"],
        "functions": changes,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--baseline", type=Path,
                        help="Compare with a JSON worklist from the PR base.")
    args = parser.parse_args(argv)
    data = inventory(args.root)
    if args.baseline is not None:
        try:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            data["comparison"] = compare_counts(baseline, data)
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            parser.error(f"Invalid AST baseline: {exc}")
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print("AST unique lines:", data["unique_lines"])
        print("Provider occurrences (overlap):", data["overlapping_occurrences_by_provider"])
        print("Responsibilities:", data["unique_lines_by_responsibility"])
        for row in data["top_functions"][:35]:
            print(f"{row['unique_lines']:3} {row['responsibility']:20} "
                  f"{row['path']}::{row['function']} {row['providers']}")
        if "comparison" in data:
            comparison = data["comparison"]
            print(f"AST count vs base: {comparison['before']} → {comparison['after']}"
                  f" ({comparison['delta']:+d})")
            for row in comparison["functions"]:
                print(f"  {row['delta']:+3d} {row['path']}::{row['function']}"
                      f" ({row['before']} → {row['after']})")
        print("Parse errors:", len(data["parse_errors"]))
    return int(bool(data["parse_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
