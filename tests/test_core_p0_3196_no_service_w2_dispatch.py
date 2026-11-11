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
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "sync_kobo_backup"
        for node in service.body
    )
    assert not any(
        isinstance(node, ast.FunctionDef)
        and node.name == "sync_kobo_backup_account"
        for node in service.body
    )
