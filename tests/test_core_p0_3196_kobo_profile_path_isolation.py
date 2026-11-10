"""#3196: read-only Kobo root selection must not import the retired W2 engine."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core.profile_source_paths import _kobo_preview_paths


def test_kobo_root_selection_is_pure_without_w2(tmp_path):
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
    assert not library.exists()


@pytest.mark.parametrize("module", [
    "source_materialization.py",
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
    # Only canonical source preparation requires the read-only Kobo root selector.
    # W4 reconciliation no longer imports it after the retired aliases vanish.
    expected = ["profile_source_paths"] if module == "source_materialization.py" else []
    assert selector_imports == expected


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


def test_gtk_maintenance_no_longer_replays_retired_kobo_w4():
    source = (
        Path(__file__).resolve().parents[1] / "e2e/library_declaration_acceptance.py"
    ).read_text(encoding="utf-8")
    # The second native W4 playback was obsolete after the 0.7 cutover.
    # The generic, independent declaration pass must still call done().
    import ast
    tree = ast.parse(source)
    recursive_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "exercise"
        and any(kw.arg == "attested" and isinstance(kw.value, ast.Constant)
                and kw.value.value is True for kw in node.keywords)
    ]
    assert recursive_calls == []
