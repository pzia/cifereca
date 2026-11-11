"""P0 #5054: no supplier ownership/preflight SQL survives in Core.

Canonical source references, acquisition ledger, artifacts and placements are
generic Core responsibilities; deleting abandoned entitlement-side readers
must not delete those shared v14 tables or their generic APIs.
"""
import ast
from pathlib import Path

from tests.test_sync_state_contract import _service


def test_legacy_numilog_purchase_sql_methods_absent():
    root = Path(__file__).resolve().parents[1]
    path = root / "src/cifereca/storage.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for retired in (
        "numilog_possession_rights",
        "numilog_purchase_preflight_evidence",
        "numilog_purchase_has_local_conflict",
    ):
        assert retired not in functions
        assert retired not in source
    assert "def acquisition_get(" in source
    assert "def provider_scope_account_bound(" in source


def test_generic_acquisition_and_origin_storage_survive_retirement(tmp_path):
    service = _service(tmp_path)
    try:
        assert service.storage.schema_version() >= 14
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.artifacts() == []
    finally:
        service.close()
