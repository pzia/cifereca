"""P0 #3196: forbid direct 0.7 backup writer calls after Kobo W2 cutover.

This static fence checks the installed Core and plugin sources; archival SQL
and generic storage compatibility APIs are tested independently.
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
    # No installed source is allowed to call the old 0.7 writer.
    # Archival APIs remain available for rollback/reconciliation.
    assert _production_legacy_calls() == {
        "backup_put": set(),
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



def test_kobo_w4_historical_writer_is_unreachable_from_production():
    """W4 legacy may remain as a test archive, not as a callable production path.

    Detect imports, direct calls, attribute access and literal getattr
    references across Core and plugins. Deleting the archive later is allowed.
    This is an architectural fence, not a replacement for transactional tests.
    """
    symbol = "_historical_reconcile_kobo_local"
    unexpected: list[str] = []
    for package in ("cifereca", "plugins"):
        directory = ROOT / "src" / package
        for path in directory.rglob("*.py"):
            if "tests" in path.relative_to(directory).parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    (isinstance(node, ast.Name) and node.id == symbol)
                    or (isinstance(node, ast.Attribute) and node.attr == symbol)
                    or (isinstance(node, ast.ImportFrom)
                        and any(alias.name == symbol for alias in node.names))
                    or (isinstance(node, ast.Constant) and node.value == symbol)
                ):
                    unexpected.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert unexpected == [], "W4 historical writer reachable: " + ", ".join(unexpected)


def test_no_historical_w4_execution_entry_in_installed_core():
    """The only historical positive W4 executor now lives under tests/."""
    target = ROOT / "src" / "cifereca" / "core" / "kobo_local_reconciliation.py"
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(target))
    forbidden = {"_historical_reconcile_kobo_local"}
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in forbidden for node in ast.walk(tree)
    )
    # Runtime lookup, not only a source-level name test.
    from cifereca.core import kobo_local_reconciliation as repair
    assert not hasattr(repair, "_historical_reconcile_kobo_local")
    assert not hasattr(repair, "reconcile_kobo_local")
