"""P0 #4859: retired Kobo W4 SQL writers exist only in test fixtures.

The historical functions must not reappear in the installed Core, even
though backwards-compatibility regression fixtures inject them at test time.
"""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.core import kobo_local_reconciliation as module


_RETIRED = {
    "_json", "_transaction", "_file",
    "_token", "_rows", "_unique_catalogue_fields", "_metadata", "_snapshot",
    "_validate", "_proposed", "_write_row", "_operation_id", "_journal",
    "_validate_undo", "_validate_historical_links",
    "_record_offline_file_evidence", "_receipt",
}
_RETIRED_CONSTANTS = {
    "_KEYS", "_CHANGES", "_JOURNAL", "_FILE_EVIDENCE_JOURNAL",
}


def test_legacy_w4_positive_engine_is_not_shipped_in_core():
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    functions = {node.name for node in tree.body if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef)
    )}
    declared = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            declared.update(target.id for target in node.targets
                            if isinstance(target, ast.Name))
    assert not (functions & _RETIRED)
    assert not (declared & _RETIRED_CONSTANTS)
    assert "observe_kobo_local_file_only" not in functions
    assert "reconcile_existing_profile" in functions
    assert "reconcile_kobo_local" not in functions
