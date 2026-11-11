"""P0 #5054 — no orphan SQL ownership mirror remains in Storage."""
import ast
from pathlib import Path

from tests.test_sync_state_contract import _service


def test_numilog_historical_right_reservations_reader_is_absent():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/cifereca/storage.py").read_text(encoding="utf-8")
    definitions = {
        node.name for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "numilog_entitlement_reservations" not in definitions
    assert "numilog_rights_index" not in source


def test_generic_canonical_tables_still_readable(tmp_path):
    service = _service(tmp_path)
    try:
        assert service.storage.schema_version() >= 14
        assert service.storage.acquisitions() == []
        assert service.storage.artifacts() == []
        assert service.storage.placements() == []
    finally:
        service.close()
