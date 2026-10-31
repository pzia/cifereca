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

DYNAMIC_IMPORT_LOADERS = ("import_module", "__import__", "spec_from_file_location", "SourceFileLoader")


def _matches_changed_module(value: str, changed_modules: dict[str, str]) -> bool:
    return any(
        value == module or value == path or value.endswith("/" + path)
        for module, path in changed_modules.items()
    )


def _contains_test_namespace(expression: ast.AST) -> bool:
    """Recognize literal namespace fragments in f-strings and concatenations."""
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value == "tests" or "tests." in node.value or "tests/" in node.value)
        for node in ast.walk(expression)
    )


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


def has_test_consumers(root: Path, changed: list[str]) -> bool:
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
    for glob in TEST_CONSUMER_GLOBS:
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
            if not mentions_changed_test and not may_import_dynamically and not may_register_pytest_plugin:
                continue
            parsed = ast.parse(source, filename=str(candidate))
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
                    if name not in DYNAMIC_IMPORT_LOADERS:
                        continue
                    for arg in node.args[:2]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if _matches_changed_module(arg.value, changed_modules):
                                return True
                        elif _contains_test_namespace(arg):
                            # Dynamic module expressions cannot be resolved
                            # without executing test code. Fail closed.
                            return True
    return False
