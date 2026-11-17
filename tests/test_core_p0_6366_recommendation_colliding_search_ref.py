"""#6366/#3104/#4979: Recommendation reuses a colliding Search row exactly.

Legacy supplier display IDs are not documentary identities. Search may
render two editions with same visible base ID but different hash-suffixed GTK
keys; detail must target the exact live SearchResult object, not a guessed
base or another edition's thumbnail/capabilities.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import ast
import sys

from cifereca.gui.recommendation_exact_cover import loaded_recommendation_cover
from tests.test_core_p1_6168_search_remote_document_gateway import host, CATALOG


def _remote_result_row_keys(rows):
    # Exercise the actual GTK-free function without importing Gtk widgets in
    # the Core-only pytest job; repository headless tests use this contract.
    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_results.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {
        "_catalogue_edition_scope", "_exact_remote_catalogue_scope",
        "_remote_result_row_keys",
    }
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(functions) == 3
    code = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__",
                       names=[ast.alias(name="annotations")], level=0),
        *functions,
    ], type_ignores=[]))
    namespace = {}
    exec(compile(code, str(source), "exec"), namespace)
    return namespace["_remote_result_row_keys"](rows)


def setup_two_versions():
    window, source, _ = host()
    version2 = replace(
        source, revision_id="edition-v2", record_id=source.record_id + "-v2",
    )
    keys = _remote_result_row_keys([source, version2])
    assert len(set(keys)) == 2
    assert all(":scope:" in key for key in keys)
    window.search_objects = dict(zip(keys, (source, version2)))
    return window, source, version2, keys


def test_colliding_supplier_display_ids_keep_two_exact_recommendation_refs():
    window, a, b, keys = setup_two_versions()
    first = loaded_recommendation_cover(window, a)
    second = loaded_recommendation_cover(window, b)
    assert first is not None and second is not None
    assert first.source_item is a and second.source_item is b
    assert first.row_key == keys[0] and second.row_key == keys[1]
    assert first.document != second.document  # Shared URL isn't a join.
    assert first.candidates[0].url == second.candidates[0].url == CATALOG
    assert first.context == second.context

    # Copying an apparently identical source is not admission of a third
    # object, nor permission to borrow another row's cached cover.
    clone = replace(a)
    assert loaded_recommendation_cover(window, clone) is None


def test_exact_recommendation_detail_updates_only_its_mapped_search_cell(
    monkeypatch,
):
    window, a, b, keys = setup_two_versions()
    rendered = [NS(key=k) for k in keys]
    window.search_page = NS(
        store=NS(
            get_n_items=lambda: len(rendered),
            get_item=lambda index: rendered[index],
        ),
    )
    published = []
    monkeypatch.setitem(
        sys.modules, "cifereca.gui.search_details",
        NS(_publish_search_detail_cover=lambda win, source, detail, *, row:
           published.append((win, source, detail, row))),
    )
    seen = object()
    proof = loaded_recommendation_cover(window, b, detail=seen)
    assert proof is not None and proof.row_key == keys[1]
    assert published == [(window, b, seen, rendered[1])]
    assert rendered[0] is not published[0][3]


def test_recommendation_fails_closed_on_duplicated_live_object_or_old_epoch():
    window, a, b, keys = setup_two_versions()
    window.search_objects[keys[1]] = a  # Ambiguous same object occurrence.
    assert loaded_recommendation_cover(window, a) is None
    assert loaded_recommendation_cover(window, b) is None
    window.search_objects[keys[1]] = b
    window._search_generation += 1  # Old Search batch, even same row/URL.
    assert loaded_recommendation_cover(window, a) is None
    assert loaded_recommendation_cover(window, b) is None
