from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "src" / "cifereca" / "services.py"


def _select_identity_source() -> str:
    tree = ast.parse(SERVICES.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "select_identity")
    return ast.unparse(method)


def test_service_identity_switch_invalidates_personal_snapshots():
    source = _select_identity_source()
    assert "self.current_identity = identity" in source
    assert "self.last_history = []" in source
    assert "self.last_bookings = []" in source
    assert source.index("self.current_identity = identity") < source.index("self.last_history = []")
    assert source.index("self.last_bookings = []") < source.index("accounts = self.accounts_for_identity")
