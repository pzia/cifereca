"""#3196: Kobo W2 backup Core module and public facade are permanently retired."""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.services import LibraryService


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca"


def test_no_kobo_backup_module_or_service_facade_can_return():
    assert not (ROOT / "core" / "kobo_backup.py").exists()
    for name in (
        "kobo_backup_enabled",
        "kobo_backup_interval_minutes",
        "kobo_backup_diagnostic",
        "sync_kobo_backup",
    ):
        assert not hasattr(LibraryService, name)


def test_core_service_and_opening_cannot_import_deleted_w2_module():
    for filename in ("services.py", "core/sync_coordination.py"):
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "kobo_backup" for alias in node.names)
            for node in ast.walk(tree)
        )
    opening = (ROOT / "core/sync_coordination.py").read_text(encoding="utf-8")
    assert "self.kobo_backup_enabled()" not in opening
    assert "self.kobo_backup_interval_minutes()" not in opening
