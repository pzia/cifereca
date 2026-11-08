"""P0 #5048: deterministic, read-only inventory of provider coupling in installed Core.

Signals are deliberately conservative, not proof that a branch executes.
No plugin imports, network access, writes, or runtime initialization.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

DEFAULT_PROVIDERS = ("kobo", "bnf", "numilog")
CORE_ROOTS = ("core", "gui", "providers")


def _provider_literal(node: ast.AST, providers: set[str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value.casefold().strip()
        if value in providers:
            return value
    return None


def inspect_source(content: str, providers: tuple[str, ...] = DEFAULT_PROVIDERS) -> dict[str, list[dict[str, object]]]:
    """Report imports of implementations and comparisons to concrete provider IDs."""
    tree = ast.parse(content)
    names = set(providers)
    imports: list[dict[str, object]] = []
    dispatches: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [("." * node.level) + (node.module or "")]
        else:
            modules = []
        for module in modules:
            parts = module.casefold().lstrip(".").split(".")
            matched = sorted(set(parts) & names)
            if matched:
                imports.append({"line": node.lineno, "module": module, "providers": matched})
        if isinstance(node, ast.Compare):
            values = [node.left, *node.comparators]
            matches = sorted({p for value in values if (p := _provider_literal(value, names))})
            if matches:
                dispatches.append({"line": node.lineno, "providers": matches})
        if isinstance(node, ast.Match):
            for case in node.cases:
                if isinstance(case.pattern, ast.MatchValue):
                    matched = _provider_literal(case.pattern.value, names)
                    if matched:
                        dispatches.append({"line": case.pattern.lineno, "providers": [matched]})
    return {"imports": sorted(imports, key=lambda x: x["line"]),
            "comparisons": sorted(dispatches, key=lambda x: x["line"])}


def inventory(root: Path, providers: tuple[str, ...] = DEFAULT_PROVIDERS) -> dict[str, object]:
    """Inspect installed Python sources, excluding plugins, tests and build artifacts."""
    src = root / "src" / "cifereca"
    findings: dict[str, object] = {}
    parse_errors: dict[str, str] = {}
    for relative_root in (*CORE_ROOTS, "kobo"):
        directory = src / relative_root
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            try:
                result = inspect_source(path.read_text(encoding="utf-8"), providers)
            except (SyntaxError, UnicodeError) as exc:
                parse_errors[relative] = type(exc).__name__
                continue
            if result["imports"] or result["comparisons"]:
                findings[relative] = result
    service = src / "services.py"
    if service.is_file():
        relative = service.relative_to(root).as_posix()
        try:
            result = inspect_source(service.read_text(encoding="utf-8"), providers)
            if result["imports"] or result["comparisons"]:
                findings[relative] = result
        except (SyntaxError, UnicodeError) as exc:
            parse_errors[relative] = type(exc).__name__
    return {"metric": "core.provider.static-coupling/1",
            "providers": list(providers),
            "files_with_signals": len(findings),
            "import_signals": sum(len(x["imports"]) for x in findings.values()),
            "comparison_signals": sum(len(x["comparisons"]) for x in findings.values()),
            "findings": dict(sorted(findings.items())),
            "parse_errors": dict(sorted(parse_errors.items())),
            "limitation": "AST signals are not reachability proofs; dynamic imports and indirect dispatch are not detected."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--providers", nargs="+", default=list(DEFAULT_PROVIDERS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = inventory(args.root, tuple(sorted({x.casefold() for x in args.providers})))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Core provider coupling: {report['files_with_signals']} files, "
              f"{report['import_signals']} imports, "
              f"{report['comparison_signals']} comparisons, "
              f"{len(report['parse_errors'])} parse errors")
    return 1 if report["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
