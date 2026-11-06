"""P0 #3196: freeze direct, active 0.7 writer call sites until cutover.

Static architectural regression, not evidence that the legacy writer has
already been retired. In particular dynamic SQL updates and confirmed repair
are listed separately in the residual-writers contract document.
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_CALLS = {"backup_put", "backup_prune_missing"}


def _production_legacy_calls() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {method: set() for method in LEGACY_CALLS}
    for package in ("cifereca", "plugins"):
        directory = ROOT / "src" / package
        for path in directory.rglob("*.py"):
            # Provider fixtures live under src/plugins/*/tests but are
            # explicitly NOT production callers of the legacy writer.
            if "tests" in path.relative_to(directory).parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in LEGACY_CALLS):
                    result[node.func.attr].add(
                        path.relative_to(ROOT / "src" / "cifereca").as_posix()
                        if package == "cifereca" else
                        "plugins/" + path.relative_to(directory).as_posix()
                    )
    return result


def test_only_reviewed_kobo_compatibility_pipeline_calls_legacy_writer():
    # The Kobo W3 caller has been retired: any new direct prune call is a
    # cutover regression. W2 backup_put remains until migration #3196.
    assert _production_legacy_calls() == {
        "backup_put": {"core/kobo_backup.py"},
        "backup_prune_missing": set(),
    }


def test_storage_still_exposes_the_historical_mutation_surface_for_cutover():
    # Prevent accidentally declaring #3196 complete while an unreviewed SQL
    # path has been removed from the inventory text: these three entrypoints
    # must each be explicitly migrated or archived, not renamed silently.
    storage = ROOT / "src" / "cifereca" / "storage.py"
    tree = ast.parse(storage.read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {"backup_put", "backup_prune_missing", "relocate_local_path"} <= names
