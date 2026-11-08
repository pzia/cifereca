"""P0 #4858: prevent W2 from bypassing its final typed-source writer fence.

Static contract: a new legacy checkpoint call must go through
_guarded_kobo_legacy_put, not call Storage.backup_put directly.
This is deliberately not evidence that the W2 0.7 executor is retired.
"""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "src" / "cifereca" / "core" / "kobo_backup.py"
)


def test_w2_storage_backup_put_only_occurs_inside_guarded_writer():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    direct: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Do not double-count calls from nested functions in their parent.
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "backup_put"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "storage"
            ):
                continue
            direct.append((node.name, child.lineno))
    assert len(direct) == 1, f"Unexpected direct W2 backup_put call sites: {direct}"
    assert direct[0][0] == "_guarded_kobo_legacy_put"


def test_w2_backup_put_aliases_delegate_to_guarded_writer():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    aliases = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "backup_put" for target in node.targets):
            continue
        aliases.append(node)
        assert isinstance(node.value, ast.Lambda), f"Unreviewed W2 writer alias at {node.lineno}"
        assert isinstance(node.value.body, ast.Call)
        assert isinstance(node.value.body.func, ast.Name)
        assert node.value.body.func.id == "_guarded_kobo_legacy_put"
    assert len(aliases) == 3, "W2 writer topology changed: review every new alias"


def test_w2_guard_checks_typed_source_before_checkpoint_write():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    guard = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_guarded_kobo_legacy_put")
    typed = [
        node.lineno for node in ast.walk(guard)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "has_typed_source"
    ]
    writes = [
        node.lineno for node in ast.walk(guard)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "backup_put"
    ]
    assert len(typed) == len(writes) == 1
    assert typed[0] < writes[0]
