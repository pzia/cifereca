"""P0 #5048: supplier-reported ownership is never an acquisition right.

The read-only GTK detail warning must not special-case Kobo. Actual
acquisition admission is still owned by the existing Core permission path.
"""
from __future__ import annotations

import ast
from pathlib import Path


DETAIL = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


def test_unverified_catalogue_ownership_notice_is_provider_neutral():
    source = DETAIL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "Acquisition locale non disponible" in source
    assert "La propriété et le téléchargement doivent être attestés" in source
    assert "elif item.source_provider == \"kobo\" and item.owned:" not in source
    assert "Sauvegarde Kobo non disponible" not in source
    assert "elif item.owned:" in source

    # The UI message is displayed in the exact no-download/no-borrow branch
    # and never changes the capability decision or adds a new action.
    match = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Attribute)
        and node.test.attr == "owned"
        and isinstance(node.test.value, ast.Name)
        and node.test.value.id == "item"
    )
    body = ast.get_source_segment(source, match)
    assert "ActionRow()" in body
    assert "set_title(\"Acquisition locale non disponible\")" in body
    assert "download_direct_item(" not in body
    assert "borrow_search_item(" not in body
    assert "can_direct_download(" not in body


def test_rights_gates_remain_external_to_owned_flag_notice():
    source = DETAIL.read_text(encoding="utf-8")
    assert "self.window.service.can_direct_download(item)" in source
    assert "if direct_download:" in source
    assert "self._direct_download_action(item)" in source
    assert "self.window.can_borrow_item(item)" in source
    assert "result_row(" in source
