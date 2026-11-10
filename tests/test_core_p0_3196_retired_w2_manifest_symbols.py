"""#3196: retired Kobo W2 interpretation must stay out of installed Core."""
from __future__ import annotations

import ast
from pathlib import Path


def test_kobo_w2_manifest_interpreters_are_removed_from_core():
    code = (Path(__file__).resolve().parents[1] /
            "src/cifereca/core/kobo_backup.py").read_text(encoding="utf-8")
    tree = ast.parse(code)
    definitions = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert definitions.isdisjoint({
        "_kobo_legacy_manifest_claim_invalid",
        "_kobo_manifest_flags_invalid",
        "_kobo_manifest_rejection_count",
        "_manifest_archived",
    })
    # W2 stays a fail-closed ABI, never a source of entitlement or GET.
    assert "kobo_legacy_w2_retired_use_canonical_provider" in code
    assert "def _sync_kobo_backup_locked(" in code
