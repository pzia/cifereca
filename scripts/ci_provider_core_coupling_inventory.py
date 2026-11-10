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
    # Full IDs in the installed BiblioAccess manifest, including the public
    # catalogue's distinct source. These are routing identities, not labels.
    "provider.biblioaccess.la-rochelle",
    "provider.biblioaccess-catalogue.la-rochelle",
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


def _immutable_import_module_constants(tree: ast.Module) -> dict[str, str]:
    """Resolve only static, unshadowed module path constants for importlib.

    No concatenation, attribute lookup, interpolation or source execution.
    Imported and locally shadowed bindings never qualify.
    """
    writes: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            writes[node.id] += 1
        elif isinstance(node, ast.arg):
            writes[node.arg] += 1
    result: dict[str, str] = {}
    for stmt in tree.body:
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)):
            name, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            name, value = stmt.target.id, stmt.value
        else:
            continue
        if (name.isupper() and writes[name] == 1
                and isinstance(value, ast.Constant)
                and type(value.value) is str and 0 < len(value.value) <= 512):
            result[name] = value.value
    return result


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


def _provider_method_name(name: str, providers: set[str]) -> list[str]:
    """Named vendor operation call, never a substring inside another word.

    Only complete identifier components are considered; strings and passive
    attribute access cannot create executable-call coupling.
    """
    if type(name) is not str or not name.isidentifier():
        return []
    parts = name.casefold().strip("_").split("_")
    # Match whole identifier components, including multi-token provider aliases
    # declared by manifests (library_example), never arbitrary substrings.
    matched = set()
    for provider in providers:
        if not provider.isidentifier():
            continue
        segments = provider.split("_")
        if any(parts[i:i + len(segments)] == segments
               for i in range(len(parts) - len(segments) + 1)):
            matched.add(provider)
    return sorted(matched)



def _aliased_getattr_provider_calls(
    tree: ast.Module, providers: set[str],
) -> list[dict[str, object]]:
    """Detect a unique local getattr alias later invoked as a provider action.

    This deliberately ignores uninvoked introspection, reassigned variables,
    shadowed builtins and cross-function bindings. A deferred provider
    callable must not evade the monotone Core coupling gate merely because
    it is assigned before execution.
    """
    findings: list[dict[str, object]] = []
    scopes = [tree, *(node for node in ast.walk(tree)
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    for scope in scopes:
        # Nested function bodies are separate bindings and must not be
        # attributed to an unrelated parent function.
        nodes: list[ast.AST] = []
        def visit(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.Lambda, ast.ClassDef)):
                    continue
                nodes.append(child)
                visit(child)
        for stmt in scope.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            nodes.append(stmt)
            visit(stmt)
        writes = Counter(
            node.id for node in nodes
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
        )
        params = set()
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = scope.args
            params = {arg.arg for arg in (
                *args.posonlyargs, *args.args, *args.kwonlyargs,
                *(tuple([args.vararg]) if args.vararg else ()),
                *(tuple([args.kwarg]) if args.kwarg else ()),
            )}
        # An explicitly rebound getattr or a local function named getattr
        # is not evidence of Python's built-in reflective call.
        if "getattr" in writes or "getattr" in params:
            continue
        for stmt in nodes:
            if (not isinstance(stmt, (ast.Assign, ast.AnnAssign))
                    or not isinstance(stmt.value, ast.Call)
                    or not isinstance(stmt.value.func, ast.Name)
                    or stmt.value.func.id != "getattr"
                    or len(stmt.value.args) < 2):
                continue
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                continue
            variable = targets[0].id
            if variable in params or writes[variable] != 1:
                continue
            attribute = stmt.value.args[1]
            if (not isinstance(attribute, ast.Constant)
                    or type(attribute.value) is not str):
                continue
            matched = set(_provider_method_name(attribute.value, providers))
            if attribute.value.casefold() in providers:
                matched.add(attribute.value.casefold())
            if not matched:
                continue
            if any(isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name)
                   and node.func.id == variable
                   and node.lineno > stmt.lineno for node in nodes):
                findings.append({"line": stmt.lineno, "providers": sorted(matched)})
    return findings


def inspect_source(content: str, providers: tuple[str, ...] = DEFAULT_PROVIDERS) -> dict[str, list[dict[str, object]]]:
    """Inventory imports, provider comparisons and executable dispatch maps."""
    tree = ast.parse(content)
    names = set(providers)
    constants = _immutable_provider_constants(tree, names)
    module_constants = _immutable_import_module_constants(tree)
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
    calls: list[dict[str, object]] = []
    method_calls: list[dict[str, object]] = []
    dynamic_calls: list[dict[str, object]] = []
    for node in ast.walk(tree):
        # Immediately invoked static getattr is executable dispatch, unlike
        # a descriptive attribute read or an uninvoked getattr result.
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Call)
                and isinstance(node.func.func, ast.Name)
                and node.func.func.id == "getattr"
                and len(node.func.args) >= 2
                and isinstance(node.func.args[1], ast.Constant)
                and type(node.func.args[1].value) is str):
            attribute = node.func.args[1].value
            matched_dynamic = _provider_method_name(attribute, names)
            if attribute.casefold() in names:
                matched_dynamic = sorted(set(matched_dynamic) | {attribute.casefold()})
            if matched_dynamic:
                dynamic_calls.append({"line": node.lineno,
                                      "providers": matched_dynamic})
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # Examples: service.sync_kobo_backup(), service.bnf_borrow().
            # Do not treat attribute reads or descriptor metadata as calls.
            matched_methods = _provider_method_name(node.func.attr, names)
            if matched_methods:
                method_calls.append({"line": node.lineno, "providers": matched_methods})
        # Only immediately invoked keyed lookups can prove executable
        # dispatch. Metadata reads and uninvoked .get calls stay neutral.
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Subscript):
                matched = _literal_providers(fn.slice, names, constants)
            elif (isinstance(fn, ast.Call)
                  and isinstance(fn.func, ast.Attribute)
                  and fn.func.attr == "get"
                  and fn.args):
                # The source object must be an explicit mapping attribute or
                # name; don't infer arbitrary calls returning callables.
                receiver = fn.func.value
                matched = (_literal_providers(fn.args[0], names, constants)
                           if isinstance(receiver, (ast.Name, ast.Attribute,
                                                    ast.Subscript))
                           else set())
            else:
                matched = set()
            if matched:
                calls.append({"line": node.lineno, "providers": sorted(matched)})
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
            module_name = None
            if dynamic and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and type(arg.value) is str:
                    module_name = arg.value
                elif isinstance(arg, ast.Name):
                    module_name = module_constants.get(arg.id)
            modules = [module_name] if module_name is not None else []
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
    dynamic_calls.extend(_aliased_getattr_provider_calls(tree, names))
    return {"imports": sorted(imports, key=lambda x: x["line"]),
            "comparisons": sorted(dispatches, key=lambda x: x["line"]),
            "dispatch_tables": sorted(tables, key=lambda x: x["line"]),
            "dispatch_calls": sorted(calls, key=lambda x: x["line"]),
            "provider_method_calls": sorted(method_calls, key=lambda x: x["line"]),
            "provider_dynamic_calls": sorted(dynamic_calls, key=lambda x: x["line"])}



# Physical LOC is complementary to AST coupling. This category definition
# preserves the historic Kobo scope of ci_kobo_core_line_inventory (#3196).
_LOC_PROVIDERS = ("kobo", "numilog", "bnf", "biblioaccess", "paris")


def _dedicated_provider(relative: Path) -> str | None:
    parts = relative.parts
    if len(parts) < 4 or parts[:2] != ("src", "cifereca"):
        return None
    name = relative.name.casefold()
    if parts[2] == "kobo":
        return "kobo"
    if parts[2] == "core":
        for provider in _LOC_PROVIDERS:
            if provider in name:
                return provider
    if parts[2] == "gui" and "legacy" in name and "kobo" in name:
        return "kobo"
    return None


def inventory(root: Path, providers: tuple[str, ...] = DEFAULT_PROVIDERS) -> dict[str, object]:
    """Inspect installed Python sources, excluding plugins, tests and build artifacts."""
    src = root / "src" / "cifereca"
    findings: dict[str, object] = {}
    parse_errors: dict[str, str] = {}
    dedicated_lines = {provider: 0 for provider in _LOC_PROVIDERS}
    dedicated_per_file: dict[str, dict[str, object]] = {}
    generic_per_file: dict[str, dict[str, object]] = {}
    # All installed source modules are Core-owned unless explicitly shipped
    # as external plugins (which live outside src/cifereca).
    if src.is_dir():
        for path in sorted(src.rglob("*.py")):
            relative_path = path.relative_to(root)
            relative = relative_path.as_posix()
            try:
                source_text = path.read_text(encoding="utf-8")
                result = inspect_source(source_text, providers)
            except (SyntaxError, UnicodeError) as exc:
                parse_errors[relative] = type(exc).__name__
                continue
            # LOC is counted only for parseable source, using exactly the
            # same physical-line convention as the existing Kobo counter.
            lines = source_text.splitlines()
            dedicated = _dedicated_provider(relative_path)
            if dedicated is not None:
                dedicated_lines[dedicated] += len(lines)
                dedicated_per_file[relative] = {
                    "provider": dedicated,
                    "lines": len(lines),
                    "nonblank_noncomment": sum(
                        bool(line.strip()) and not line.lstrip().startswith("#")
                        for line in lines
                    ),
                }
            else:
                mentions = {
                    provider: sum(provider in line.casefold() for line in lines)
                    for provider in _LOC_PROVIDERS
                }
                if any(mentions.values()):
                    generic_per_file[relative] = {
                        "lines": len(lines),
                        "provider_literal_lines": mentions,
                        "mixed_provider_lines": sum(
                            any(provider in line.casefold() for provider in _LOC_PROVIDERS)
                            for line in lines
                        ),
                    }
            if any(result[category] for category in (
                "imports", "comparisons", "dispatch_tables", "dispatch_calls",
                "provider_method_calls", "provider_dynamic_calls",
            )):
                findings[relative] = result
    return {"metric": "core.provider.static-coupling/1",
            "providers": list(providers),
            "files_with_signals": len(findings),
            "import_signals": sum(len(x["imports"]) for x in findings.values()),
            "comparison_signals": sum(len(x["comparisons"]) for x in findings.values()),
            "dispatch_table_signals": sum(len(x["dispatch_tables"]) for x in findings.values()),
            "dispatch_call_signals": sum(len(x["dispatch_calls"]) for x in findings.values()),
            "provider_method_call_signals": sum(len(x["provider_method_calls"]) for x in findings.values()),
            "provider_dynamic_call_signals": sum(len(x["provider_dynamic_calls"]) for x in findings.values()),
            "findings": dict(sorted(findings.items())),
            "parse_errors": dict(sorted(parse_errors.items())),
            "dedicated_lines": sum(dedicated_lines.values()),
            "dedicated_files": len(dedicated_per_file),
            "dedicated_by_provider": dedicated_lines,
            "dedicated_per_file": dedicated_per_file,
            "generic_files_with_provider_literal": len(generic_per_file),
            "generic_provider_literal_lines_unique": sum(
                row["mixed_provider_lines"] for row in generic_per_file.values()
            ),
            "generic_per_file": generic_per_file,
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
              f"{report['dispatch_call_signals']} provider-keyed calls, "
              f"{report['provider_method_call_signals']} provider method calls, "
              f"{report['provider_dynamic_call_signals']} dynamic provider calls, "
              f"{len(report['parse_errors'])} parse errors; "
              f"{report['dedicated_lines']} dedicated provider LOC / "
              f"{report['dedicated_files']} files; "
              f"{report['generic_provider_literal_lines_unique']} unique "
              f"generic-file provider-mention lines")
    return 1 if report["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
