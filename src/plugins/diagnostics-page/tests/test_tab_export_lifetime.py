"""Execute the actual unmap callback: hidden tabs are not detached details."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("attached,same_root,closed", [
    (True, True, False), (False, True, True), (True, False, True),
])
def test_tab_hide_preserves_export_but_pagination_and_rebuild_revoke_it(
    attached, same_root, closed,
):
    source = Path(__file__).parents[1] / "cifereca_diagnostics_page.py"
    tree = ast.parse(source.read_text())
    callback = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "on_detail_unmap")
    pending = []
    exporter = Mock()
    root = object()
    page = SimpleNamespace(_page_root=root if same_root else object(),
                           _support_exports=[exporter])
    namespace = {"self": page, "page_root": root, "json_export": exporter,
                 "expander": SimpleNamespace(is_ancestor=lambda value: attached),
                 "GLib": SimpleNamespace(idle_add=lambda callback: pending.append(callback))}
    exec(compile(ast.Module(body=[callback], type_ignores=[]), str(source), "exec"), namespace)
    namespace["on_detail_unmap"]()
    exporter.close.assert_not_called()  # GTK removal has not finished yet.
    assert len(pending) == 1
    assert pending[0]() is False
    assert exporter.close.called is closed
    assert (exporter in page._support_exports) is (not closed)
