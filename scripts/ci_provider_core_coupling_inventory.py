"""P0 #5048: deterministic, read-only inventory of provider coupling in installed Core.

Signals are deliberately conservative, not proof that a branch executes.
No plugin imports, network access, writes, or runtime initialization.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

DEFAULT_PROVIDERS = ("kobo", "bnf", "bnf-pret", "bnf-catalogue", "numilog")


def _provider_literal(node: ast.AST, providers: set[str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value.casefold().strip()
        if value in providers:
            return value
    return None



def _literal_providers(node: ast.AST, providers: set[str]) -> set[str]:
    """Bounded static ID alternatives, never evaluate expressions or calls."""
    direct = _provider_literal(node, providers)
    if direct is not None:
        return {direct}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return {value for item in node.elts
                if (value := _provider_literal(item, providers)) is not None}
    return set()


def _match_providers(pattern: ast.pattern, providers: set[str]) -> set[str]:
    if isinstance(pattern, ast.MatchValue):
        return _literal_providers(pattern.value, providers)
    if isinstance(pattern, ast.MatchOr):
        return set().union(*(_match_providers(part, providers) for part in pattern.patterns))
    return set()


def inspect_source(content: str, providers: tuple[str, ...] = DEFAULT_PROVIDERS) -> dict[str, list[dict[str, object]]]:
    """Report imports of implementations and comparisons to concrete provider IDs."""
    tree = ast.parse(content)
    names = set(providers)
    # Import names are Python identifiers; provider IDs may contain hyphens.
    import_names = {name for name in names if name.isidentifier()}
    imports: list[dict[str, object]] = []
    dispatches: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            stem = ("." * node.level) + (node.module or "")
            # Count an import statement once when its module is already
            # provider-specific; inspect imported aliases for generic parents.
            stem_parts = stem.casefold().lstrip(".").split(".")
            is_provider_module = any(
                part == provider or part.startswith(provider + "_")
                for part in stem_parts for provider in import_names
            )
            modules = [stem] if is_provider_module else [
                stem + "." + alias.name for alias in node.names
            ]
        else:
            modules = []
        for module in modules:
            parts = module.casefold().lstrip(".").split(".")
            matched = sorted({provider for part in parts for provider in import_names
                              if part == provider or part.startswith(provider + "_")})
            if matched:
                imports.append({"line": node.lineno, "module": module, "providers": matched})
        if isinstance(node, ast.Compare):
            values = [node.left, *node.comparators]
            matches = sorted(set().union(*(
                _literal_providers(value, names) for value in values
            )))
            if matches:
                dispatches.append({"line": node.lineno, "providers": matches})
        if isinstance(node, ast.Match):
            for case in node.cases:
                matched = sorted(_match_providers(case.pattern, names))
                if matched:
                    dispatches.append({"line": case.pattern.lineno, "providers": matched})
    return {"imports": sorted(imports, key=lambda x: x["line"]),
            "comparisons": sorted(dispatches, key=lambda x: x["line"])}


def inventory(root: Path, providers: tuple[str, ...] = DEFAULT_PROVIDERS) -> dict[str, object]:
    """Inspect installed Python sources, excluding plugins, tests and build artifacts."""
    src = root / "src" / "cifereca"
    findings: dict[str, object] = {}
    parse_errors: dict[str, str] = {}
    # All installed source modules are Core-owned unless explicitly shipped
    # as external plugins (which live outside src/cifereca).
    if src.is_dir():
        for path in sorted(src.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            try:
                result = inspect_source(path.read_text(encoding="utf-8"), providers)
            except (SyntaxError, UnicodeError) as exc:
                parse_errors[relative] = type(exc).__name__
                continue
            if result["imports"] or result["comparisons"]:
                findings[relative] = result
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
