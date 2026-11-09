"""P0 #4859: retired W4 public shell is independent of W2 private paths."""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.core import kobo_local_reconciliation as retired


def test_retired_w4_does_not_import_w2_preview_or_keep_legacy_path_helpers():
    source = Path(retired.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    assert "_kobo_preview_paths" not in imports
    assert "kobo_backup" not in source
    functions = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not {"_roots", "_within"} & functions
    assert {"reconcile_kobo_local", "reconcile_existing_profile"} <= functions
