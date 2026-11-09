"""P0 #4859: retired W4 must not import the legacy W2 preview backend."""
import ast
from pathlib import Path

from cifereca.core.source_materialization import _within


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/core"


def test_w4_has_no_import_of_w2_backup_or_w2_path_helpers():
    source = (ROOT / "kobo_local_reconciliation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module in ("kobo_backup", "source_materialization")
        for node in ast.walk(tree)
    )
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "_roots" not in functions
    assert "_within" not in functions
    assert "reconcile_existing_profile" in functions


def test_w2_private_path_filter_preserves_nested_absolute_boundaries(tmp_path):
    root = (tmp_path / "alice").resolve()
    root.mkdir()
    assert _within(str(root / "book.epub"), (root,))
    assert not _within(str(root), (root,))
    assert not _within(str(tmp_path / "alice-other" / "book.epub"), (root,))
    assert not _within(str(root / ".." / "bob" / "book.epub"), (root,))
    assert not _within("relative.epub", (root,))
    assert not _within("", (root,))
    assert not _within(str(root / "book.epub") + "\0", (root,))
