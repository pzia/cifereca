"""#4682: the approved Detail mockup has no redundant visual 'Essentiel' heading.

Keep the internal section key for ordering and the field/action contract.
This source-level widget gate can run in CI without a graphical display.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.gui_source import gui_source_text


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    tree = ast.parse(gui_source_text(MAIN))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    method = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_essential_section_heading_is_visually_hidden_only():
    group = _method("_group")
    assert "group.set_title('' if title == 'Essentiel' else title)" in group
    assert "grid.set_max_children_per_line(2)" in group
    assert "detail_subtitle_lines(title, display_value)" in group
    assert "self._add_metadata_actions(row, label, safe_value, field.kind)" in group
    assert "row.set_title(label)" in group
    assert "group.add(grid)" in group
    assert "parent.append(group)" in group


def test_essential_fields_and_summary_order_remain_in_shared_renderer():
    cover = _method("_cover_and_title")
    renderer = _method("show_presentation")
    assert "self._group('Essentiel'," in cover
    assert "fields=essential" in cover
    assert "essential=essential" in renderer
    assert "section.title == 'Essentiel'" in renderer
    assert renderer.index("essential=essential") < renderer.index(
        "self._show_description(description)"
    )
    assert renderer.index("self._show_description(description)") < renderer.index(
        "self._group(section.title, rows)"
    )


def test_other_section_headings_keep_their_visible_titles():
    group = _method("_group")
    assert "group.set_title('' if title == 'Essentiel' else title)" in group
    assert "group.set_title('')" not in group
    assert "section.title == 'Détail'" in _method("show_presentation")
