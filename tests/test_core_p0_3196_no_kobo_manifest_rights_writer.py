"""Core P0 #3196: private catalogue rows cannot mint Kobo purchase rights."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "src" / "cifereca"


def _function(path: str, function: str, *, klass: str | None = None) -> ast.AST:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    scope = tree.body
    if klass:
        scope = next(
            node.body for node in scope
            if isinstance(node, ast.ClassDef) and node.name == klass
        )
    return next(node for node in scope
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function)


def test_private_catalogue_sync_never_mints_kobo_rights() -> None:
    for path, name, klass in (
        ("services.py", "mirror_personal_libraries", "LibraryService"),
        ("core/sync_coordination.py", "_synchronize_account_source_impl", None),
    ):
        target = _function(path, name, klass=klass)
        assert not any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and "kobo_manifest_rights" in ast.unparse(node)
            for node in ast.walk(target)
        ), f"{path}:{name} imports the legacy manifest projector"
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "mirror_kobo_manifest_rights"
            for node in ast.walk(target)
        ), f"{path}:{name} must not grant ownership from SearchResult"


def test_typed_rights_admission_is_separate_from_catalogue_mirroring() -> None:
    source = (ROOT / "core" / "rights_snapshot_read.py").read_text(encoding="utf-8")
    assert "def read_account_rights_snapshot(" in source
    assert "provider_scope_account_bound(" in source
    assert "rights_observation_snapshot" in source
    assert "if raw_reader is None:" in source
