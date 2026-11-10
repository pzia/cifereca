"""#3196: retired Kobo direct GET cannot be advertised by the legacy action gate."""
from __future__ import annotations

import ast
from pathlib import Path


def test_kobo_direct_download_hint_denies_before_session_or_rights_lookup():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/services.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "LibraryService")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "can_direct_download")
    assert any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and ast.unparse(node.test) == "item.source_provider == 'kobo'"
        and any(isinstance(statement, ast.Return)
                and isinstance(statement.value, ast.Constant)
                and statement.value.value is False for statement in node.body)
        for node in method.body
    )
    # Keep the provider-specific direct path physically absent, not merely
    # gated behind a flag that another caller might change later.
    assert "kobo_personal_right_states" not in ast.unparse(method)
    assert "resolve_acquisition" not in ast.unparse(method)
    assert "has_complete_delivery_abi" not in ast.unparse(method)
