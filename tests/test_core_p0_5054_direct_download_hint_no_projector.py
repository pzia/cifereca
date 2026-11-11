"""P0 #5054: GUI/CLI passive action hint cannot import a rights projector."""
from __future__ import annotations

import ast
from pathlib import Path


def test_direct_download_hint_validates_only_origin_record_shape():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/services.py"
    src = path.read_text(encoding="utf-8")
    fn = next(
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "can_direct_download"
    )
    modules = [
        node.module for node in ast.walk(fn)
        if isinstance(node, ast.ImportFrom)
    ]
    assert "core.numilog_manifest_rights" not in modules
    assert "core.credential_scoped_record" not in modules
    assert "core.private_inventory_row_scope" in modules
    body = ast.get_source_segment(src, fn)
    assert "selected_private_offer_hint(" in body
    assert "numilog" not in body.lower()
    guard = (path.parent / "core/private_inventory_row_scope.py").read_text(
        encoding="utf-8",
    )
    assert "credential_scoped_private_inventory_rows_valid(" in guard
    record = (path.parent / "core/credential_scoped_record.py").read_text(
        encoding="utf-8",
    )
    assert "def exact_credential_scoped_record(" in record
