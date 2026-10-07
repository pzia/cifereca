from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/action_widgets.py"


def _method(name: str):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AdaptiveActionBar")
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _standalone_static(name: str):
    method = _method(name)
    fn = ast.FunctionDef(
        name=method.name,
        args=method.args,
        body=method.body,
        decorator_list=[],
        returns=method.returns,
        type_comment=method.type_comment,
    )
    ns = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), str(SOURCE), "exec"), ns)
    return ns[name]


def test_duplicate_send_icons_keep_short_destination_labels():
    compact_label = _standalone_static("_compact_label")

    assert compact_label("Envoyer à Héloïse", "document-send-symbolic", 2) == "→ Héloïse"
    assert compact_label("Envoyer à Alexandre", "document-send-symbolic", 2) == "→ Alexandre"


def test_unique_icon_can_still_compact_to_icon_only():
    compact_label = _standalone_static("_compact_label")

    assert compact_label("Ouvrir", "document-open-symbolic", 1) is None
    assert compact_label("Télécharger", "document-save-symbolic", 1) is None


def test_other_duplicate_icons_keep_their_full_labels():
    compact_label = _standalone_static("_compact_label")

    assert compact_label("Action A", "view-more-symbolic", 2) == "Action A"
    assert compact_label("Action B", "view-more-symbolic", 2) == "Action B"


def test_resize_counts_icons_before_compacting():
    source = ast.unparse(_method("_resize"))

    assert "icon_counts" in source
    assert "self._compact_label(label, icon, icon_counts.get(icon, 0))" in source
    assert "compact and compact_label is None" in source
    assert "shown_label = compact_label if compact else label" in source
