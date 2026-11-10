"""#3196: read-only Kobo root selection must not import the retired W2 engine."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core.profile_source_paths import _kobo_preview_paths
from cifereca.core.kobo_backup import _kobo_preview_paths as legacy_alias


def test_kobo_root_selection_is_pure_and_keeps_legacy_abi(tmp_path):
    library = tmp_path / "library"
    account = SimpleNamespace(identity_id="alice", directory_name="Alice")
    service = SimpleNamespace(
        config=SimpleNamespace(get=lambda key, default=None: {
            "library.directory": str(library),
            "kobo_backup.directory": "archives",
        }.get(key, default)),
        identity_by_id=lambda identity: account if identity == "alice" else None,
    )
    expected = (library, library / "archives" / "Alice" / "kobo")
    assert _kobo_preview_paths(service, "alice") == expected
    assert legacy_alias(service, "alice") == expected
    assert not library.exists()


@pytest.mark.parametrize("module", [
    "source_materialization.py",
    "kobo_local_audit.py",
    "kobo_local_reconciliation.py",
])
def test_read_only_consumers_do_not_import_retired_backup_engine(module):
    import ast
    path = Path(__file__).resolve().parents[1] / "src/cifereca/core" / module
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selector_imports = [
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "_kobo_preview_paths" for alias in node.names)
    ]
    assert selector_imports == ["profile_source_paths"]


def test_root_selector_keeps_missing_identity_read_only(tmp_path):
    library = tmp_path / "offline"
    service = SimpleNamespace(
        config=SimpleNamespace(get=lambda key, default=None: {
            "library.directory": str(library),
            "kobo_backup.directory": "",
        }.get(key, default)),
        identity_by_id=lambda identity: None,
    )
    assert _kobo_preview_paths(service, "unknown") == (
        library, library / "unknown" / "kobo",
    )
    assert not library.exists()
