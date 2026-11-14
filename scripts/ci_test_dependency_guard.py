"""Detect consumers of changed Core tests before targeting pytest.

Core test modules sometimes export fixtures used by other test modules.
"""
from __future__ import annotations

import ast
from pathlib import Path

TEST_CONSUMER_GLOBS = (
    "tests/**/*.py",
    "src/plugins/*/tests/**/*.py",
    "scripts/test*.py",
)

# These APIs execute or import test modules just like importlib. Missing one
# means a changed shared Core test can leave its consumers unselected.
DYNAMIC_IMPORT_LOADERS = (
    "import_module", "__import__", "spec_from_file_location", "SourceFileLoader",
    "importorskip", "run_module", "run_path",
)


def _matches_changed_module(value: str, changed_modules: dict[str, str]) -> bool:
    return any(
        value == module or value == path or value.endswith("/" + path)
        for module, path in changed_modules.items()
    )


def _contains_test_namespace(
    expression: ast.AST, changed_modules: dict[str, str]
) -> bool:
    """Only ambiguous prefixes of changed tests require a full run.

    Fully named *other* modules (including plugin test paths) are not
    dependencies on the changed Core test. Do not broaden on any "tests/"
    substring: imported test modules can be deliberately unrelated.
    """
    for node in ast.walk(expression):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        part = node.value
        if part == "tests":
            return True
        if not part.startswith(("tests.", "tests/")):
            continue
        if any(module.startswith(part) or path.startswith(part)
               for module, path in changed_modules.items()):
            return True
    return False


def _pytest_plugins_reuses_changed_test(value: ast.AST, changed_modules: dict[str, str]) -> bool:
    """An unresolved pytest plugin module is a potential shared-test consumer."""
    entries = value.elts if isinstance(value, (ast.Tuple, ast.List, ast.Set)) else [value]
    for entry in entries:
        if isinstance(entry, ast.Constant) and isinstance(entry.value, str):
            if _matches_changed_module(entry.value, changed_modules):
                return True
        else:
            # pytest_plugins accepts dynamically computed module names. Without
            # evaluation the safe choice is to run all Core tests.
            return True
    return False


def has_test_consumers(
    root: Path, changed: list[str], *,
    consumer_globs: tuple[str, ...] = TEST_CONSUMER_GLOBS,
) -> bool:
    """Return whether another test imports one of the changed test modules.

    Parse candidates that mention the changed names. Data strings and comments
    do not count as imports. Unknown dynamic imports of the test namespace
    force full selection. Callers must fail closed on I/O or parser errors.
    """
    changed_modules = {
        path.removesuffix(".py").replace("/", "."): path
        for path in changed
        if path.startswith("tests/test_") and path.endswith(".py")
    }
    if not changed_modules:
        return False
    stems = {module.rsplit(".", 1)[-1] for module in changed_modules}
    # Dynamic imports can construct "tests.<name>" without spelling out a
    # changed test module. Inert namespace references do not need AST parsing.
    namespace_fragments = ('"tests"', "'tests'", "tests.", "tests/")
    for glob in consumer_globs:
        for candidate in root.glob(glob):
            if not candidate.is_file() or candidate.relative_to(root).as_posix() in changed_modules.values():
                continue
            if candidate.is_symlink():
                return True
            source = candidate.read_text(encoding="utf-8", errors="replace")
            mentions_changed_test = any(stem in source for stem in stems)
            may_import_dynamically = any(fragment in source for fragment in namespace_fragments) and any(
                loader in source for loader in DYNAMIC_IMPORT_LOADERS
            )
            may_register_pytest_plugin = "pytest_plugins" in source
            # Alias imports may hide the canonical loader name at the call
            # site, but the import declaration itself spells it explicitly.
            imported_loader = any(
                "import " + loader in source or "import (" in source
                for loader in DYNAMIC_IMPORT_LOADERS
            )
            if (not mentions_changed_test and not may_import_dynamically
                    and not may_register_pytest_plugin
                    and not (imported_loader and any(
                        fragment in source for fragment in namespace_fragments
                    ))):
                continue
            parsed = ast.parse(source, filename=str(candidate))
            # Follow only namespace values assigned to names used by a
            # dynamic import. Looking for "tests/" anywhere in the AST
            # incorrectly treats unrelated test data as import provenance
            # (e.g. selector tests containing path strings).
            namespace_names: set[str] = set()
            assignments = [
                node for node in ast.walk(parsed)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
            ]
            for _ in range(len(assignments) + 1):
                changed = False
                for assignment in assignments:
                    value = assignment.value
                    if value is None:
                        continue
                    if not (_contains_test_namespace(value, changed_modules)
                            or any(isinstance(part, ast.Name)
                                   and part.id in namespace_names
                                   for part in ast.walk(value))):
                        continue
                    targets = (
                        assignment.targets if isinstance(assignment, ast.Assign)
                        else [assignment.target]
                    )
                    for target in targets:
                        for part in ast.walk(target):
                            if isinstance(part, ast.Name) and part.id not in namespace_names:
                                namespace_names.add(part.id)
                                changed = True
                if not changed:
                    break
            # Keep import aliases local to each consumer module. A helper
            # imported as `from importlib import import_module as load`
            # is still an import loader, even though its call is `load(...)`.
            dynamic_loader_aliases = set(DYNAMIC_IMPORT_LOADERS)
            for import_node in ast.walk(parsed):
                if not isinstance(import_node, ast.ImportFrom):
                    continue
                if import_node.module not in {
                    "importlib", "importlib.util", "importlib.machinery",
                    "builtins", "pytest", "runpy",
                }:
                    continue
                for alias in import_node.names:
                    if alias.name in DYNAMIC_IMPORT_LOADERS:
                        dynamic_loader_aliases.add(alias.asname or alias.name)
            for node in ast.walk(parsed):
                if isinstance(node, ast.Import):
                    if any(alias.name == module or alias.name.startswith(module + ".")
                           for alias in node.names for module in changed_modules):
                        return True
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module in changed_modules:
                        return True
                    if node.level and module in stems:
                        return True
                    if module in {"tests", ""} and any(alias.name in stems for alias in node.names):
                        return True
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(target, ast.Name) and target.id == "pytest_plugins"
                           for target in targets):
                        if node.value is not None and _pytest_plugins_reuses_changed_test(
                            node.value, changed_modules
                        ):
                            return True
                elif isinstance(node, ast.Call):
                    function = node.func
                    name = function.id if isinstance(function, ast.Name) else (
                        function.attr if isinstance(function, ast.Attribute) else ""
                    )
                    if name not in dynamic_loader_aliases:
                        continue
                    for arg in node.args[:2]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if _matches_changed_module(arg.value, changed_modules):
                                return True
                        elif (_contains_test_namespace(arg, changed_modules)
                              or any(isinstance(part, ast.Name)
                                     and part.id in namespace_names
                                     for part in ast.walk(arg))):
                            # Unresolved variable/alias may have been assembled
                            # from a test namespace elsewhere in this module.
                            # Fail closed instead of silently skipping plugins.
                            return True
    return False
