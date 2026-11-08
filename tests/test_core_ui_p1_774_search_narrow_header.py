"""P1 #774: keep native Search actions accessible at physical 1024px."""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_controller.py"


def test_search_moves_existing_action_bar_to_its_own_row_before_filters():
    source = SOURCE.read_text(encoding="utf-8")
    module = ast.parse(source)
    build = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_search_page"
    )
    body = ast.get_source_segment(source, build)
    assert body is not None
    # No new action bar, action callback, search engine or provider switch.
    moved = "page.page_header.remove(page.header_actions)"
    inserted = "page.insert_child_after(page.header_actions, page.page_header)"
    assert body.count(moved) == 1
    assert body.count(inserted) == 1
    assert body.count("page.header_actions.source = page") == 1
    assert body.index(moved) < body.index(inserted)
    assert body.index(inserted) < body.index("page.toolbar.append(self.search_controls)")
    assert body.count("page.page_header.insert_child_after(") == 1
    assert "page.page_header.insert_child_after(self._search_response_facets, page.count_label)" in body
    assert "page.header_actions = " not in body


def test_search_1024_keeps_real_x11_geometry_and_xtest_gates():
    source = (SOURCE.parents[2] / "e2e/search_filter_probe.py").read_text(
        encoding="utf-8"
    )
    assert "assert image.size == (1024, 768)" in source
    assert "assert 924 <= allocated_width <= 1024" in source
    assert "device.click_widget(controls.entry)" in source
    assert "P1_3687_search_real_1024_geometry" in source
