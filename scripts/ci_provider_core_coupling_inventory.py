"""P0 #5048: deterministic, read-only inventory of provider coupling in installed Core.

Signals are deliberately conservative, not proof that a branch executes.
No plugin imports, network access, writes, or runtime initialization.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path

DEFAULT_PROVIDERS = (
    "kobo", "bnf", "bnf-pret", "bnf-catalogue", "numilog", "paris",
    # Package IDs and legacy spelling variants are real routing aliases,
    # not neutral capabilities. No partial/substring matches for literals.
    "provider-kobo", "provider-numilog", "provider-bnf-pret",
    "provider-bnf-catalogue", "provider-paris", "bnf_pret", "bnf_catalogue",
    # Interactive tenant plugin and canonical dotted ID. A provider that is
    # new to Core must not hide behind a hyphen, a dotted identifier or an
    # underscore alias just because Python imports use different spelling.
    "biblioaccess", "biblioaccess.la-rochelle",
    "biblioaccess_la_rochelle", "provider-biblioaccess",
)


def _provider_literal(node: ast.AST, providers: set[str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value.casefold().strip()
        if value in providers:
            return value
    return None


def _immutable_provider_constants(tree: ast.Module, providers: set[str]) -> dict[str, str]:
    """Identify unambiguous MODULE constants, never execute source code.

    Only ALL_CAPS simple assignments with one write anywhere in the AST are
    eligible. A local shadow/reassignment invalidates the entire binding.
    Dynamic or imported values are intentionally ignored.
    """
    writes: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            writes[node.id] += 1
        elif isinstance(node, ast.arg):
            writes[node.arg] += 1
    constants: dict[str, str] = {}
    for stmt in tree.body:
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)):
            name, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            name, value = stmt.target.id, stmt.value
        else:
            continue
        if not name.isupper() or writes[name] != 1 or value is None:
            continue
        provider = _provider_literal(value, providers)
        if provider is not None:
            constants[name] = provider
    return constants


def _literal_providers(
    node: ast.AST, providers: set[str], constants: dict[str, str] | None = None,
) -> set[str]:
    """Bounded static ID alternatives, never evaluate expressions or calls."""
    constants = constants or {}
    direct = _provider_literal(node, providers)
    if direct is None and isinstance(node, ast.Name):
        direct = constants.get(node.id)
    if direct is not None:
        return {direct}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set().union(*(_literal_providers(item, providers, constants) for item in node.elts))
    return set()


def _match_providers(
    pattern: ast.pattern, providers: set[str], constants: dict[str, str],
) -> set[str]:
    if isinstance(pattern, ast.MatchValue):
        return _literal_providers(pattern.value, providers, constants)
    if isinstance(pattern, ast.MatchOr):
        return set().union(*(_match_providers(part, providers, constants) for part in pattern.patterns))
    return set()


def _known_callable_symbols(tree: ast.Module) -> set[str]:
    """Unambiguous locally defined function names; no execution or inference."""
    declared: set[str] = set()
    overwritten: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in declared:
                overwritten.add(node.name)
            declared.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            overwritten.add(node.id)
        elif isinstance(node, ast.arg):
            # A parameter can shadow a global function when referenced inside
            # a nested dispatcher; avoiding a missed signal matters less than
            # avoiding a false-positive provider-keyed data lookup.
            overwritten.add(node.arg)
    return declared - overwritten


_DISPATCH_HINTS = ("dispatch", "handler", "route", "executor", "callback", "operation", "action")


def _dispatch_table_literal(
    node: ast.Dict, *, providers: set[str], functions: set[str],
    constants: dict[str, str], hint: bool,
) -> list[str]:
    """Only callable-valued provider-keyed maps are *executable* dispatch."""
    matched: set[str] = set()
    for key, value in zip(node.keys, node.values):
        if key is None:
            continue
        names = _literal_providers(key, providers, constants)
        if not names:
            continue
        callable_value = (
            isinstance(value, ast.Lambda)
            or isinstance(value, ast.Name) and (value.id in functions or hint)
            or isinstance(value, ast.Attribute) and hint
            or isinstance(value, ast.Call) and hint
        )
        if callable_value:
            matched.update(names)
    return sorted(matched)


def inspect_source(content: str, providers: tuple[str, ...] = DEFAULT_PROVIDERS) -> dict[str, list[dict[str, object]]]:
    """Inventory imports, provider comparisons and executable dispatch maps."""
    tree = ast.parse(content)
    names = set(providers)
    constants = _immutable_provider_constants(tree, names)
    # Track actual importlib bindings before accepting dynamic module loading.
    importlib_names: set[str] = set()
    import_module_names: set[str] = set()
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name == "importlib":
                    importlib_names.add(alias.asname or alias.name)
        elif isinstance(stmt, ast.ImportFrom) and stmt.module == "importlib":
            for alias in stmt.names:
                if alias.name == "import_module":
                    import_module_names.add(alias.asname or alias.name)
    # Import names are Python identifiers; provider IDs may contain hyphens.
    import_names = {name for name in names if name.isidentifier()}
    functions = _known_callable_symbols(tree)
    # The declaring assignment can establish a named dispatch table. The
    # reference is the *exact AST dict node*; names in text/docs do not count.
    dispatch_hints: set[int] = set()
    for stmt in ast.walk(tree):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            value = stmt.value
            targets = stmt.targets if isinstance(stmt, ast.Assign) else (stmt.target,)
            if isinstance(value, ast.Dict) and any(
                isinstance(target, (ast.Name, ast.Attribute))
                and any(token in (target.id if isinstance(target, ast.Name) else target.attr).casefold()
                        for token in _DISPATCH_HINTS)
                for target in targets
            ):
                dispatch_hints.add(id(value))
    imports: list[dict[str, object]] = []
    dispatches: list[dict[str, object]] = []
    tables: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            matched = _dispatch_table_literal(
                node, providers=names, functions=functions,
                constants=constants, hint=id(node) in dispatch_hints,
            )
            if matched:
                tables.append({"line": node.lineno, "providers": matched})
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
        elif isinstance(node, ast.Call):
            # Closed set: importlib.import_module, its explicitly imported
            # aliases, and Python's built-in __import__. No generic getattr
            # or arbitrary function with an import-like name.
            fn = node.func
            dynamic = (
                isinstance(fn, ast.Name)
                and (fn.id == "__import__" or fn.id in import_module_names)
            ) or (
                isinstance(fn, ast.Attribute) and fn.attr == "import_module"
                and isinstance(fn.value, ast.Name)
                and fn.value.id in importlib_names
            )
            modules = (
                [node.args[0].value]
                if dynamic and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str) else []
            )
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
                _literal_providers(value, names, constants) for value in values
            )))
            if matches:
                dispatches.append({"line": node.lineno, "providers": matches})
        if isinstance(node, ast.Match):
            for case in node.cases:
                matched = sorted(_match_providers(case.pattern, names, constants))
                if matched:
                    dispatches.append({"line": case.pattern.lineno, "providers": matched})
    return {"imports": sorted(imports, key=lambda x: x["line"]),
            "comparisons": sorted(dispatches, key=lambda x: x["line"]),
            "dispatch_tables": sorted(tables, key=lambda x: x["line"])}


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
            if any(result[category] for category in (
                "imports", "comparisons", "dispatch_tables",
            )):
                findings[relative] = result
    return {"metric": "core.provider.static-coupling/1",
            "providers": list(providers),
            "files_with_signals": len(findings),
            "import_signals": sum(len(x["imports"]) for x in findings.values()),
            "comparison_signals": sum(len(x["comparisons"]) for x in findings.values()),
            "dispatch_table_signals": sum(len(x["dispatch_tables"]) for x in findings.values()),
            "findings": dict(sorted(findings.items())),
            "parse_errors": dict(sorted(parse_errors.items())),
            "limitation": "AST signals, not reachability proofs; indirect dynamic dispatch may remain."}


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
              f"{report['dispatch_table_signals']} dispatch tables, "
              f"{len(report['parse_errors'])} parse errors")
    return 1 if report["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
