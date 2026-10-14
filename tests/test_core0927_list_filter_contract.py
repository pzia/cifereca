from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.gui.query_model import QueryState, apply_facet


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def test_query_state_filters_loaded_rows_without_mutating_input():
    rows = [
        {"key": "a", "title": "Dumas", "author": "Alexandre Dumas", "provider_id": "paris", "media_format": "epub"},
        {"key": "b", "title": "Autre", "author": "Autre", "provider_id": "gallica", "media_format": "pdf"},
    ]
    before = repr(rows)

    result = QueryState("dumas", field="author", providers=("paris",), types=("epub",)).filter(rows)

    assert [row["key"] for row in result] == ["a"]
    assert repr(rows) == before


def test_common_facets_keep_source_format_and_support_context():
    state = QueryState(
        "ancien",
        field="title",
        scope="digital",
        providers=("paris",),
        types=("epub",),
        languages=("fr",),
    )

    series = apply_facet(state, "series", "Cycle D'Artagnan")
    assert series.field == "all"
    assert series.text == "Cycle D'Artagnan"
    assert series.scope == "digital"
    assert series.providers == ("paris",)
    assert series.types == ("epub",)
    assert series.languages == ("fr",)

    author = apply_facet(series, "author", "Auteur : Dumas, Alexandre")
    assert author.field == "author"
    assert author.text == "Dumas Alexandre"
    assert author.providers == ("paris",)
    assert author.types == ("epub",)


def test_filtering_local_rows_does_not_require_service_or_network():
    state = QueryState("roman", field="subject")
    rows = [{"key": "a", "title": "Livre", "metadata": {"subjects": ["Roman historique"]}}]

    assert [row["key"] for row in state.filter(rows)] == ["a"]


def test_plugin_page_rebuild_preserves_search_and_library_query_states():
    owner = SOURCE.parent / "shell_page_controller.py"
    tree = ast.parse(owner.read_text())
    method = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "rebuild_plugin_pages")
    text = ast.unparse(method)

    assert "controls.state()" in text
    constants = {node.value for node in ast.walk(method) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert {"search", "library"} <= constants
    assert "controls.set_state(state)" in text


def test_query_state_is_immutable_value_model():
    state = QueryState("dumas", providers=("paris",))
    changed = apply_facet(state, "format", "KEPUB")

    assert state.types == ()
    assert changed.types == ("epub",)
    assert changed.providers == ("paris",)
