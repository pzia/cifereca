"""P0 #5054: no provider purchase-entitlement projection remains in Core."""
from pathlib import Path
import ast


def test_numilog_manifest_projector_is_physically_absent():
    root = Path(__file__).resolve().parents[1] / "src/cifereca"
    assert not (root / "core/numilog_manifest_rights.py").exists()
    for filename in ("services.py", "storage.py"):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and "numilog_manifest_rights" in node.module
            for node in ast.walk(tree)
        )


def test_numilog_live_binding_stays_independent_from_projector():
    root = Path(__file__).resolve().parents[1] / "src/cifereca/core"
    for filename in (
        "numilog_private_rows.py", "numilog_manual_acquisition.py",
        "numilog_owned_download.py",
    ):
        source = (root / filename).read_text(encoding="utf-8")
        assert "numilog_manifest_rights" not in source
