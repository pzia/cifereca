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
    dynamic_loaders = ("import_module", "__import__", "spec_from_file_location", "SourceFileLoader")
    for glob in TEST_CONSUMER_GLOBS:
        for candidate in root.glob(glob):
            if not candidate.is_file() or candidate.relative_to(root).as_posix() in changed_modules.values():
                continue
            if candidate.is_symlink():
                return True
            source = candidate.read_text(encoding="utf-8", errors="replace")
            mentions_changed_test = any(stem in source for stem in stems)
            may_import_dynamically = "tests." in source and any(
                loader in source for loader in dynamic_loaders
            )
            if not mentions_changed_test and not may_import_dynamically:
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
                elif isinstance(node, ast.Call):
                    function = node.func
                    name = function.id if isinstance(function, ast.Name) else (
                        function.attr if isinstance(function, ast.Attribute) else ""
                    )
                    if name not in {"import_module", "__import__", "spec_from_file_location", "SourceFileLoader"}:
                        continue
                    for arg in node.args[:2]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            value = arg.value
                            if any(value == module or value.endswith("/" + path) or value == path
                                   for module, path in changed_modules.items()):
                                return True
                        elif isinstance(arg, ast.JoinedStr):
                            if any(isinstance(part, ast.Constant) and isinstance(part.value, str)
                                   and "tests." in part.value for part in arg.values):
                                return True
    return False
