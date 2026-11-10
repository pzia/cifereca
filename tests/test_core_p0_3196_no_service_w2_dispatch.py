"""#3196: legacy W2 cannot be dispatched by the public service facade."""
from __future__ import annotations

import ast
from pathlib import Path


def test_kobo_service_facades_have_no_w2_executor_call() -> None:
    services = Path(__file__).resolve().parents[1] / "src" / "cifereca" / "services.py"
    source = services.read_text(encoding="utf-8")
    tree = ast.parse(source)
    service = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LibraryService"
    )
    for name in ("sync_kobo_backup", "sync_kobo_backup_account"):
        method = next(
            node for node in service.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "kobo_backup"
            and node.func.attr == "sync_kobo_backup"
            for node in ast.walk(method)
        ), f"{name} must not dispatch W2"
        assert any(
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "ProviderConfigurationError"
            for node in ast.walk(method)
        ), f"{name} must reject missing canonical ABI"
